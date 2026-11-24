"""ICT SMC alerts: same signal rules as the EA, but it only sends alerts (no orders).

Runs on Termux with the Python standard library only (no pip install needed).
Prices come from free public sources, so they can differ from your broker's prices.

Environment variables (all optional):
    SYMBOLS           comma list, default BTCUSD,XAUUSD,US100,US30,EURUSD,GBPJPY,GBPUSD,USDJPY (JPYGBP is opt-in)
    OFFSET_<SYMBOL>   e.g. OFFSET_XAUUSD=-12.5, added to feed prices (broker price - feed price)
    MIN_SCORE         0-4: ICT alerts need at least this many classical confirmations (default 0)
    FUTURES           1 = futures for gold/indices instead of spot; or a list: FUTURES=US100,US30
    BALANCE           your account balance in $: adds the lot size to each alert
    RISK_PERCENT      risk per trade in % (default 0.25, paper), used for the lot size
    CONTRACT_<SYMBOL> your own assumption of units per 1 lot (only used when markets.json is not filled)
    SESSION_ONLY      1 = no alerts outside London / New York hours
    NEWS              0 = turn off the high-impact news calendar (default on)
    NEWS_BLOCK        1 = no alerts from 15 min before to 30 min after high-impact news
    EVENTS            heads-up alerts, comma list of sweep,fvg,ob (default all three; EVENTS=none turns off)
    SESSION_ALERTS    0 = no alert when the London / New York session opens (default on)
    TIMEFRAMES        comma list from 1m,5m,15m,1h,4h,1d (default all six)
    EVENT_TIMEFRAMES  timeframes that get sweep/FVG/OB heads-up alerts (default 15m,1h,4h,1d)
    RCT               0 turns off the reversal / continuation alerts
    US_OPEN_ALERTS    0 = no alert 15 min before / at the US open (09:30 New York)
    US_OPEN_ONLY      1 = entry signals only from 30 min before to 2 hours after the US open
    TRACK             0 = do not follow signals to report WIN / LOSS (results go to results.csv)
    CLASSIC           0 turns off the standalone classical alerts (default 1)
    ALERT_SOUND       path of your own sound file, or "off"; default is alert.wav next to this script
    REVIEW_COPY       1 (default): every entry signal is copied to the clipboard with a review request,
                      to paste into an AI assistant; 0 turns it off (also saved in last_signal_review.txt)
    TELEGRAM_TOKEN    Telegram bot token (to also get alerts in Telegram)
    TELEGRAM_CHAT_ID  your Telegram chat id
"""
import json
import logging
import os
import termfix

import builtins

_builtin_print = builtins.print


def print(*args, **kwargs):          # noqa: A001 - Arabic on the Termux screen is shaped (see termfix.py); notifications are untouched
    _builtin_print(*[termfix.shape(a) for a in args], **kwargs)


import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import alert_classes
import rejections
import reversal_machine
import reversal_tracker
import decisions
import asia_breakout
import displacement_alerts
import session_alerts
import flip_ifvg
import market_state
import ny_session
import data_layer
import events
import extras
import markets
import rct
import selective
import signal_store
import structure
import tracker
from classic import find_classic_setup, ict_confluence, trend
from ict_smc_bot import Config, find_setup, to_series

log = logging.getLogger("ict_alerts")

TF_SECONDS = data_layer.TF_SECONDS
PROFILE = {"1m": "scalp", "5m": "scalp", "15m": "scalp", "1h": "swing", "4h": "swing", "1d": "swing"}
BAR_SECONDS = 300  # default timeframe (5m)
# Yahoo has no 4h candles: they are built from 1h candles. (interval, range) per timeframe.
YAHOO_TF = {"1m": ("1m", "5d"), "5m": ("5m", "5d"), "15m": ("15m", "1mo"),
            "1h": ("60m", "3mo"), "4h": ("60m", "1y"), "1d": ("1d", "2y")}
LAST_R = {}  # (symbol, timeframe) -> candles of the last analysis, newest first
TREND_CACHE = {}  # (symbol, timeframe) -> 'up' / 'down' / 'flat', filled while analysing


stale_after = data_layer.stale_after

# name -> free data source. Default is SPOT prices: bitcoin from Binance, gold from
# Yahoo spot XAUUSD=X, indices from the Yahoo cash indices ^NDX and ^DJI.
# FUTURES=1 switches gold/indices to futures (GC=F, NQ=F, YM=F).
INSTRUMENTS = {
    "BTCUSD": {"source": "binance", "id": "BTCUSDT", "digits": 2},
    "XAUUSD": {"source": "yahoo", "id": "XAUUSD=X", "digits": 2, "fallbacks": ["binance:PAXGUSDT", "GC=F"]},
    "US100": {"source": "yahoo", "id": "^NDX", "digits": 1},
    "US30": {"source": "yahoo", "id": "^DJI", "digits": 1},
    "EURUSD": {"source": "yahoo", "id": "EURUSD=X", "digits": 5},
    "GBPJPY": {"source": "yahoo", "id": "GBPJPY=X", "digits": 3},
    "GBPUSD": {"source": "yahoo", "id": "GBPUSD=X", "digits": 5},
    "USDJPY": {"source": "yahoo", "id": "USDJPY=X", "digits": 3},
    "DAX40": {"source": "yahoo", "id": "^GDAXI", "digits": 1},      # opt-in: SYMBOLS=...,DAX40 (cash index, Xetra hours)
    "JPYGBP": {"source": "yahoo", "id": "JPYGBP=X", "digits": 6},   # opt-in: SYMBOLS=...,JPYGBP
}
FUTURES_IDS = {"XAUUSD": "GC=F", "US100": "NQ=F", "US30": "YM=F"}
for _n in INSTRUMENTS:  # FALLBACK_XAUUSD=GC=F,binance:PAXGUSDT overrides the stand-in order ("none" = no fallback)
    _fb = os.environ.get(f"FALLBACK_{_n}")
    if _fb:
        INSTRUMENTS[_n]["fallbacks"] = [] if _fb.lower() == "none" else [x.strip() for x in _fb.split(",")]
# The US index CFDs trade almost 24 h, but the cash indices (^NDX, ^DJI) only update 09:30-16:00 New York.
# So US100 / US30 use the E-mini futures (NQ=F, YM=F, ~23 h a day) by default; US_FEED=cash goes back to
# the cash indices (then the bot says "market closed" outside the US session).
for _n in INSTRUMENTS:  # FEED_XAUUSD=binance:PAXGUSDT uses that feed first (no Yahoo at all); FEED_US30=yahoo:YM=F too
    _feed = os.environ.get(f"FEED_{_n}", "").strip()
    if ":" in _feed:
        _src, _fid = _feed.split(":", 1)
        if _src.lower() in ("binance", "yahoo") and _fid:
            INSTRUMENTS[_n].update(source=_src.lower(), id=_fid, fallbacks=[])
_fut = os.environ.get("FUTURES", "US100,US30" if os.environ.get("US_FEED", "futures") != "cash" else "").strip().upper()
for _n, _id in FUTURES_IDS.items():  # FUTURES=1 for all, or a list such as FUTURES=US100,US30
    if _fut == "1" or _n in [x.strip() for x in _fut.split(",")]:
        INSTRUMENTS[_n]["id"] = _id


TD_TF = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1day"}
TD_DEFAULT_SYMBOLS = {"US100": "NDX", "US30": "DJI"}      # Twelve Data names; override with TWELVEDATA_SYMBOL_US100 / _US30
if os.environ.get("TWELVEDATA_API_KEY", "").strip():   # a free Twelve Data key adds a second source for the US indices
    for _n, _sym in TD_DEFAULT_SYMBOLS.items():
        if _n in INSTRUMENTS and not os.environ.get(f"FALLBACK_{_n}"):
            INSTRUMENTS[_n]["fallbacks"] = list(INSTRUMENTS[_n].get("fallbacks", [])) + \
                ["twelvedata:" + (os.environ.get(f"TWELVEDATA_SYMBOL_{_n}") or _sym)]


# ===== Data =====

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def parse_binance(rows):
    return [{
        "time": datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc),
        "open": float(r[1]), "high": float(r[2]),
        "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]) if len(r) > 5 else 0.0,
    } for r in rows]


def parse_twelvedata(data):
    """Candles (oldest first, UTC) from a Twelve Data time_series answer requested with order=asc&timezone=UTC."""
    if not isinstance(data, dict) or data.get("status") == "error" or "values" not in data:
        raise ValueError("Twelve Data: " + str((data or {}).get("message", "no values"))[:120])
    out = []
    for v in data["values"]:
        t = datetime.strptime(v["datetime"][:19].replace("T", " "), "%Y-%m-%d %H:%M:%S" if len(v["datetime"]) > 10 else "%Y-%m-%d")
        out.append({"time": t.replace(tzinfo=timezone.utc), "open": float(v["open"]), "high": float(v["high"]),
                    "low": float(v["low"]), "close": float(v["close"]), "volume": float(v.get("volume") or 0)})
    return sorted(out, key=lambda c: c["time"])


def _twelvedata_candles(symbol, tf):
    key = os.environ.get("TWELVEDATA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Twelve Data: TWELVEDATA_API_KEY is not set")
    url = (f"https://api.twelvedata.com/time_series?symbol={urllib.parse.quote(symbol, safe='')}&interval={TD_TF[tf]}"
           f"&outputsize=300&order=asc&timezone=UTC&apikey={urllib.parse.quote(key, safe='')}")
    try:
        return parse_twelvedata(fetch_json(url))
    except Exception as e:                                   # never let the key reach a log line
        raise RuntimeError(str(e).replace(key, "***")) from None


def parse_yahoo(data):
    result = data["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(result.get("timestamp") or []):
        o, h, l, c = quote["open"][i], quote["high"][i], quote["low"][i], quote["close"][i]
        if None in (o, h, l, c):
            continue
        vols = quote.get("volume") or []
        out.append({"time": datetime.fromtimestamp(t, tz=timezone.utc),
                    "open": o, "high": h, "low": l, "close": c,
                    "volume": float(vols[i] or 0) if i < len(vols) else 0.0})
    return out


def resample(candles, seconds):
    """Merge candles (oldest first) into bigger ones aligned to multiples of `seconds` (UTC)."""
    out = []
    for c in sorted(candles, key=lambda c: c["time"]):
        start = int(c["time"].timestamp()) // seconds * seconds
        if out and out[-1]["start"] == start:
            o = out[-1]
            o["high"] = max(o["high"], c["high"])
            o["low"] = min(o["low"], c["low"])
            o["close"] = c["close"]
            o["volume"] += c.get("volume") or 0.0
        else:
            out.append({"start": start, "open": c["open"], "high": c["high"],
                        "low": c["low"], "close": c["close"], "volume": c.get("volume") or 0.0})
    return [{"time": datetime.fromtimestamp(o["start"], tz=timezone.utc), "open": o["open"],
             "high": o["high"], "low": o["low"], "close": o["close"], "volume": o["volume"]} for o in out]


YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
FALLBACK_SECONDS = 1800
_STICKY = {}  # primary symbol -> (fallback symbol, until timestamp)
_CACHE = {}
CACHE_TTL = 45  # seconds: the engines share one download per symbol and timeframe


def _drop_cache(symbol):
    for key in [k for k in _CACHE if k[1] == symbol]:
        _CACHE.pop(key, None)


def get_candles(inst, tf="5m"):
    """Candles for (symbol, timeframe), re-using a download younger than CACHE_TTL seconds so
    the alert engines and the selective engine do not hit the free feeds twice."""
    key = (inst["source"], inst["id"], tf)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return list(hit[1])
    candles = _fetch_candles(inst, tf)
    _CACHE[key] = (time.time(), candles)
    return list(candles)


def _binance_klines(symbol, tf):
    last_error = None
    for host in ("https://api.binance.com", "https://data-api.binance.vision"):
        try:
            rows = fetch_json(f"{host}/api/v3/klines?symbol={symbol}&interval={tf}&limit=300")
            return parse_binance(rows)
        except Exception as e:  # try the next host
            last_error = e
    raise last_error


def _fetch_candles(inst, tf="5m"):
    if inst["source"] == "binance":
        return _binance_klines(inst["id"], tf)
    interval, span = YAHOO_TF[tf]
    primary = inst["id"]
    ids = [primary] + list(inst.get("fallbacks", []))
    sticky = _STICKY.get(primary)
    if sticky and time.time() < sticky[1]:
        ids = [sticky[0]]            # keep ONE symbol for all timeframes while a fallback is in use
    elif sticky:
        _STICKY.pop(primary, None)   # the fallback period ended: try the primary symbol again
        _drop_cache(primary)
    last_error = None
    for sid in ids:
        if sid.startswith("binance:"):  # a Binance market used as a stand-in (e.g. PAXGUSDT for gold)
            try:
                candles = _binance_klines(sid.split(":", 1)[1], tf)
            except Exception as e:
                last_error = e
                continue
            if sid != primary and (primary not in _STICKY):
                _STICKY[primary] = (sid, time.time() + FALLBACK_SECONDS)
                _drop_cache(primary)
                print(f"DATA SOURCE: {primary} is unavailable ({last_error}); using {sid} for "
                      f"{FALLBACK_SECONDS // 60} min. Its price differs from spot: set OFFSET.", flush=True)
            return candles[-300:]
        if sid.startswith("twelvedata:"):               # keyed free fallback (TWELVEDATA_API_KEY): cash index, not futures
            try:
                candles = _twelvedata_candles(sid.split(":", 1)[1], tf)
            except Exception as e:
                last_error = e
                continue
            if sid != primary and (primary not in _STICKY):
                _STICKY[primary] = (sid, time.time() + FALLBACK_SECONDS)
                _drop_cache(primary)
                print(f"DATA SOURCE: {primary} is unavailable ({last_error}); using {sid} for "
                      f"{FALLBACK_SECONDS // 60} min. Its price differs from your broker: set OFFSET.", flush=True)
            return candles[-300:]
        for host in YAHOO_HOSTS:
            sym = urllib.parse.quote(sid, safe="")
            url = f"https://{host}/v8/finance/chart/{sym}?interval={interval}&range={span}"
            try:
                candles = parse_yahoo(fetch_json(url))
            except Exception as e:   # next host, then next symbol
                last_error = e
                continue
            if tf == "4h":
                candles = resample(candles, TF_SECONDS["4h"])
            if sid != primary and (primary not in _STICKY):
                _STICKY[primary] = (sid, time.time() + FALLBACK_SECONDS)
                _drop_cache(primary)  # never mix two symbols' prices in one analysis
                print(f"DATA SOURCE: {primary} is unavailable ({last_error}); using {sid} for "
                      f"{FALLBACK_SECONDS // 60} min. Its price differs from spot: set OFFSET.", flush=True)
            return candles[-300:]
    raise last_error


def fallback_note(inst):
    """Text for alerts when a fallback symbol is feeding the analysis, else ''."""
    sticky = _STICKY.get(inst["id"])
    if sticky and time.time() < sticky[1]:
        return (f"DATA SOURCE WARNING: {inst['id']} is unavailable, prices come from {sticky[0]} "
                f"(not spot). Compare with MT5 and set OFFSET before trading.")
    return ""


# ===== Analysis =====

def format_alert(kind, name, setup, price, d, cfg, r, points, offset):
    risk = abs(setup["entry"] - setup["sl"])
    side = setup["side"].upper()
    if kind == "ict":
        head = f"ICT SMC ALERT: {side} LIMIT {name}"
        extra = f"Confluence: {len(points)}/4" + (f" ({', '.join(points)})" if points else "") + "\n"
    elif kind == "rct":
        mode = setup.get("mode", "continuation")
        if mode == "continuation":
            head = f"RCT ALERT: {side} CONTINUATION {name} (trend + EMA pullback + resume candle)"
        else:
            head = f"RCT ALERT: {side} REVERSAL {name} (RSI stretched + sweep + rejection candle)"
        extra = "Entry is the signal candle close: place it as a limit order at that price\n"
        if mode != "continuation":
            extra += "Counter-trend: higher risk, use a smaller size\n"
    else:
        head = f"CLASSIC ALERT: {side} {name} (trend + fib zone + reversal candle)"
        extra = "Entry is the reversal candle close: place it as a limit order at that price\n"
    note = "(offset applied)" if offset else "(feed price: compare with MT5 and shift the levels if it differs)"
    return (
        f"{head}\n"
        f"Entry: {setup['entry']:.{d}f}\n"
        f"SL:    {setup['sl']:.{d}f}\n"
        f"TP:    {setup['tp']:.{d}f}\n"
        f"Risk distance: {risk:.{d}f} | RR {cfg.rr}\n"
        f"{extra}"
        f"Price now: {price:.{d}f} {note}\n"
        f"Candle: {r[1]['time'].strftime('%Y-%m-%d %H:%M')} UTC"
    )


def analyze(name, cfg, now_ts, seen, offsets=None, opts=None, tf="5m"):
    """Returns (status_text, alerts); alerts is a list of {'kind', 'setup', 'text'}.

    offsets: optional {name: number} added to every feed price so the levels match
    your broker (offset = broker price - feed price).
    opts: optional {'balance', 'contracts': {name: units}, 'news': [events],
    'session_only': bool, 'news_block': bool} for the extra lines and filters.
    """
    opts = opts or {}
    inst = INSTRUMENTS[name]
    bar = TF_SECONDS[tf]
    candles = get_candles(inst, tf)
    if not candles:
        return f"{name}: no data", []

    offset = (offsets or {}).get(name, 0.0)
    if offset:
        candles = [dict(c, open=c["open"] + offset, high=c["high"] + offset,
                        low=c["low"] + offset, close=c["close"] + offset)
                   for c in candles]

    r = to_series(candles)
    forming = now_ts - now_ts % bar
    newest = int(r[0]["time"].timestamp())

    if now_ts - newest > stale_after(bar):
        age_min = (now_ts - newest) // 60
        return f"{name}: market closed or data delayed (last candle {age_min} min old)", []

    quality = data_layer.check_candles(candles, tf, now_ts, inst["source"] == "binance", min_bars=1)
    if quality.blocked:
        return f"{name}: {quality.summary()}", []

    # The signal rules need r[0] to be the bar that is still forming. If the newest bar
    # has already ended, the feed has not listed the forming bar yet: add a placeholder.
    if newest + bar <= now_ts:
        placeholder = dict(r[0])
        placeholder["time"] = datetime.fromtimestamp(forming, tz=timezone.utc)
        r.insert(0, placeholder)

    d = inst["digits"]
    price = r[0]["close"]
    status = f"{name}: price {price:.{d}f} ok"
    if offset:
        status += f" (feed {price - offset:.{d}f}, offset {offset:+g})"
    if quality.warnings:
        status += " [" + "; ".join(quality.warnings) + "]"

    bar_time = int(r[1]["time"].timestamp())
    LAST_R[(name, tf)] = r
    alerts = []
    my_trend = trend(r, cfg)
    if my_trend:
        TREND_CACHE[(name, tf)] = my_trend

    # entry rule uses the close of the last CLOSED candle, never the still-forming one
    ref_price = r[1]["close"]
    setup = find_setup(r, cfg, ask=ref_price, bid=ref_price, digits=d)
    if setup and opts.get("legacy_ict", True):
        points = ict_confluence(r, cfg, setup)
        key = (name, tf, "ict", bar_time)
        if len(points) >= cfg.min_score and key not in seen:
            seen.add(key)
            alerts.append({"kind": "ict", "setup": setup,
                           "text": format_alert("ict", name, setup, price, d, cfg, r, points, offset)})

    if cfg.classic_enabled:
        classic = find_classic_setup(r, cfg)
        key = (name, tf, "classic", bar_time)
        if classic and key not in seen:
            seen.add(key)
            classic = {k: (round(v, d) if isinstance(v, float) else v) for k, v in classic.items()}
            alerts.append({"kind": "classic", "setup": classic,
                           "text": format_alert("classic", name, classic, price, d, cfg, r, [], offset)})

    if cfg.rct_enabled:
        sig = rct.find_rct(r, cfg)
        key = (name, tf, "rct", bar_time)
        if sig and key not in seen:
            seen.add(key)
            sig = {k: (round(v, d) if isinstance(v, float) else v) for k, v in sig.items()}
            alerts.append({"kind": "rct", "setup": sig,
                           "text": format_alert("rct", name, sig, price, d, cfg, r, [], offset)})

    for ev in events.find_events(r, cfg, d, opts.get("events", ())):
        key = (name, tf, ev["kind"], ev["side"], bar_time)
        if key not in seen:
            seen.add(key)
            alerts.append({"kind": "event", "setup": None, "side": ev["side"],
                           "text": f"{ev['kind'].upper()} ALERT {name} ({ev['side'].upper()} bias)\n"
                                   f"{ev['text']}\nPrice now: {price:.{d}f}\n"
                                   f"Candle: {r[1]['time'].strftime('%Y-%m-%d %H:%M')} UTC"})
    for a in alerts:
        a.update(name=name, tf=tf, bar_time=bar_time)
    for a in alerts:  # second line: timeframe and style (scalp / swing)
        a["text"] = a["text"].replace("\n", f"\nTimeframe: {tf} ({PROFILE[tf]})\n", 1)
    return add_extras(name, alerts, cfg, now_ts, opts, status, tf)


def add_extras(name, alerts, cfg, now_ts, opts, status, tf="5m"):
    """Adds session, lot and news lines to each alert, and applies the optional filters."""
    if not alerts:
        return status, alerts
    sessions = extras.session_text(now_ts)
    if opts.get("session_only") and not extras.active_sessions(now_ts):
        return status + " (signal skipped: outside sessions)", []
    if opts.get("us_open_only") and extras.us_open_state(now_ts) is None and \
            any(a["kind"] != "event" for a in alerts):
        alerts = [a for a in alerts if a["kind"] == "event"]
        if not alerts:
            return status + " (signal skipped: outside US open window)", []
    news = extras.news_line(opts.get("news") or [], name, now_ts)
    if news and opts.get("news_block"):
        return status + " (signal skipped: news)", []
    contract = (opts.get("contracts") or {}).get(name)  # only when you set CONTRACT_<SYMBOL>
    spec = (opts.get("markets") or {}).get(markets.canonical(name))
    for a in alerts:
        s = a["setup"]
        lot = ""
        if s:
            risk = abs(s["entry"] - s["sl"])
            lot = extras.lot_text(opts.get("balance", 0), cfg.risk_percent, s, spec, contract)
        side = s["side"] if s else a.get("side")
        a["text"] += (f"\nSession: {sessions}\n" + extras.us_open_line(now_ts) + news + lot
                      + extras.trend_line(TREND_CACHE, name, tf, side))
        a["text"] = a["text"].rstrip("\n")
    return status, alerts


# ===== Selective engine (H4/H1 trend + S/R, M15 FVG/OB, M5 closed confirmation) =====

SELECTIVE_TFS = (("5m", "m5"), ("15m", "m15"), ("1h", "h1"), ("4h", "h4"))
OPTIONAL_TFS = (("1d", "d1"), ("1m", "m1"))  # context / timing only; a failure here never blocks


US_INDEX_MARKETS = ("US30", "NAS100")


ZONE_SEEN_FILE = os.environ.get("ZONE_SEEN_FILE", "zone_seen.json")


def _load_zone_seen(path=None, now=None, keep=24 * 3600):
    """Zones already explained (id -> time), read from disk so a restart does not repeat them; old ones are dropped."""
    try:
        with open(path or ZONE_SEEN_FILE) as f:
            raw = json.load(f)
        now = time.time() if now is None else now
        return {k: int(v) for k, v in raw.items() if now - int(v) < keep}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _save_zone_seen(seen, path=None):
    """Writes the explained zones atomically; a write problem is ignored (the explanation may then repeat once)."""
    p = path or ZONE_SEEN_FILE
    try:
        with open(p + ".tmp", "w") as f:
            json.dump(seen, f)
        os.replace(p + ".tmp", p)
    except OSError:
        pass


_ALERT_STATE = None


def alert_state():
    """The persistent setup tracker state (alerts_state.json), loaded on first use."""
    global _ALERT_STATE
    if _ALERT_STATE is None:
        _ALERT_STATE = alert_classes.AlertState()
    return _ALERT_STATE


def ny_view(data, now_ts):
    """New York session picture (print-only, never a trade decision). NY_SESSION=0 turns it off. Returns the ny_session
    result or None when there is no M15 data or anything fails."""
    if os.environ.get("NY_SESSION", "1") == "0":
        return None
    try:
        bars = selective.closed_only(data.get("m15") or [], 900, now_ts)
        trend = structure.current_trend(structure.structure_events(selective.closed_only(data.get("h1") or [], 3600, now_ts), 3)) \
            if data.get("h1") else None
        return ny_session.analyze(bars, now_ts, trend)
    except Exception:
        return None


def state_view(data, now_ts, calendar="utc"):
    """Market state text for the scan line (print-only). MARKET_STATE=0 turns it off; any failure gives ''."""
    if os.environ.get("MARKET_STATE", "1") == "0":
        return ""
    try:
        bars = selective.closed_only(data.get("m15") or [], 900, now_ts)
        h1 = selective.closed_only(data.get("h1") or [], 3600, now_ts)
        trend = structure.current_trend(structure.structure_events(h1, 3)) if h1 else None
        return market_state.classify(bars, trend, calendar)["state"]
    except Exception:
        return ""


def h1_trend(data, now_ts):
    """H1 structure trend ('up' / 'down' / None) from closed candles, or None."""
    try:
        h1 = selective.closed_only(data.get("h1") or [], 3600, now_ts)
        return structure.current_trend(structure.structure_events(h1, 3)) if h1 else None
    except Exception:
        return None


def track_setups(market, name, data, now_ts, cfg, calendar, lang, store=None, reasons=None, digits=2, gate=None, signals=None):
    """Runs the setup tracker for one market: the six alert classes, the reversal state machine (one setup per direction,
    one direction at a time) and the rejection log. Returns the text lines to print (and phones the alerts).
    Nothing returned here is ever a trade signal, except what the normal signal path sends."""
    observations, last_close = selective.watch_observations(market, data, now_ts, cfg, calendar, lang,
                                                            alert_classes.APPROACH_ATR)
    state = alert_state()
    skip = decisions.ignored()                                    # setups you chose to IGNORE are finished for the bot
    for sid in skip:
        if sid in state.setups and not state.setups[sid].get("done"):
            state.setups[sid]["done"] = "ignored"
    observations = [o for o in observations if o["sid"] not in skip]
    try:
        import zones_report
        zones_report.record(market, observations, last_close, now_ts, h1_trend(data, now_ts))
    except Exception:                                             # the zones report must never stop the scan
        pass
    active = {sg["side"] for sg in store.active_list(market)} if store is not None else set()
    kept, notes = reversal_machine.arbitrate(state, market, observations, now_ts, h1_trend(data, now_ts), active)
    extra = ""
    for _kind, sid, text in notes:
        extra += "\n" + text
        rejections.log(market, state.setups[sid]["side"], "SUPPRESSED", [text], now_ts)
    for cls, sid, text in alert_classes.process(state, market, kept, last_close, now_ts, lang):
        extra += "\n" + text
        alert_classes.deliver(cls, text)
    for kind, sid, text in reversal_machine.update(state, market, kept, now_ts, "en"):   # stage lines are screen-only: English
        extra += "\n" + text
        if kind == "EXPIRED":
            rejections.log(market, state.setups[sid]["side"], "EXPIRED", [text], now_ts)
    if os.environ.get("FLIP_IFVG", "1") != "0":                    # state flip / IFVG watch (analysis only, never fails a scan)
        try:
            m15 = selective.closed_only(data.get("m15") or [], 900, now_ts)
            m5 = selective.closed_only(data.get("m5") or [], 300, now_ts)
            for cls, sid, text in flip_ifvg.process(state, market, m15, m5, h1_trend(data, now_ts), now_ts, lang, digits, calendar,
                                                    gate if os.environ.get("IFVG_SIGNALS", "1") != "0" else None):
                if cls == "DROP":
                    rejections.log(market, state.setups[sid]["side"] if sid in state.setups else None, "IFVG", [text], now_ts)
                    continue
                if cls == "IFVG_SIGNAL" and signals is not None:            # a real signal: the caller notifies and tracks it
                    plan = state.setups[sid]["plan"]
                    signals.append({"text": text, "setup": plan, "market": market, "decision_id": sid, "bar_time": plan["bar_time"],
                                    "id": f"{market}-{plan['side']}-{plan['bar_time']}"})
                    continue
                extra += "\n" + text
                if sid in skip:
                    continue
                alert_classes.deliver(cls, text, sid)
        except Exception as e:                                       # noqa: BLE001
            log.warning("flip/IFVG watch failed for %s: %s", market, e)
    if asia_breakout.enabled(market):                               # gold Asia-range breakout (signals only, never fails a scan)
        try:
            m15 = selective.closed_only(data.get("m15") or [], 900, now_ts)
            h1 = selective.closed_only(data.get("h1") or [], 3600, now_ts)
            for cls, sid, text in asia_breakout.process(state, market, m15, h1, h1_trend(data, now_ts), now_ts, lang, digits, calendar,
                                                        gate if os.environ.get("IFVG_SIGNALS", "1") != "0" else None):
                if cls == "DROP":
                    rejections.log(market, state.setups[sid]["side"] if sid in state.setups else None, "ASIA", [text], now_ts)
                    continue
                if cls == "ASIA_SIGNAL" and signals is not None:
                    plan = state.setups[sid]["plan"]
                    signals.append({"text": text, "setup": plan, "market": market, "decision_id": sid, "bar_time": plan["bar_time"],
                                    "tag": "ASIA", "id": f"{market}-{plan['side']}-{plan['bar_time']}"})
                    continue
                extra += "\n" + text
                if sid in skip:
                    continue
                alert_classes.deliver(cls, text, sid)
        except Exception as e:                                       # noqa: BLE001
            log.warning("Asia breakout failed for %s: %s", market, e)
    if displacement_alerts.enabled(market):                          # sweep / displacement state machine (alert only, never fails a scan)
        try:
            m15d = selective.closed_only(data.get("m15") or [], 900, now_ts)
            h1d = selective.closed_only(data.get("h1") or [], 3600, now_ts)
            for cls, sid, text in displacement_alerts.process(market, m15d, h1d, digits):
                extra += "\n" + text
                if cls != "DISP_MISSED" and sid not in skip:
                    alert_classes.deliver(cls, text, sid)
        except Exception as e:                                       # noqa: BLE001
            log.warning("displacement engine failed for %s: %s", market, e)
    if session_alerts.enabled(market):                               # ICT Session Scalper / SMC Prime / Order Flow Pulse (alert only)
        try:
            m5s = selective.closed_only(data.get("m5") or [], 300, now_ts)
            m15s = selective.closed_only(data.get("m15") or [], 900, now_ts)
            h1s = selective.closed_only(data.get("h1") or [], 3600, now_ts)
            for cls, sid, text in session_alerts.process(market, m5s, m15s, h1s, h1_trend(data, now_ts), digits):
                extra += "\n" + text
                if cls != "SESSION_INFO" and sid not in skip:
                    alert_classes.deliver(cls, text, sid)
        except Exception as e:                                       # noqa: BLE001
            log.warning("session alerts failed for %s: %s", market, e)
    for o in kept:
        idx = reversal_machine.target_stage(o)
        rejections.log(market, o["side"], reversal_machine.stage_name(idx, o["side"]), reasons or ["waiting for the next stage"], now_ts)
    if not observations:
        rejections.log(market, None, "NO SETUP", reasons or ["no sweep with a structure break"], now_ts)
    state.save()
    return extra


_ZONE_SEEN = _load_zone_seen()   # zone id -> time it was first explained


def wait_line(name, res):
    """The screen line for a WAIT. A setup that passed every rule and only missed the score gate is shown
    as WATCH (print-only, no sound): it is the closest thing to an entry the engine has seen."""
    why = res["reasons"][0] if res["reasons"] else "no setup"
    if res.get("band") in ("WATCH", "REJECT") and "score100" in res and why.startswith("score "):
        line = f"{name}: {'WATCH' if res['band'] == 'WATCH' else 'near miss'} ({why.split('. Parts')[0]})"
    else:
        line = f"{name}: SELECTIVE WAIT ({why})"
    stage = res["report"].get("reversal", "")
    if stage.startswith("stage") and not stage.startswith(("stage 0", "stage 1")):
        line += f" [setup forming: reversal {stage.split(':')[0].split(' complete')[0]}]"
    return line


def reason_key(status):
    """The short reason of a scan line, numbers removed, so equal blockers are counted together."""
    import re
    m = re.search(r"\((.*?)\)", status)
    why = m.group(1) if m else status.split(":", 1)[-1]
    if "MARKET CLOSED" in status:
        why = "market closed"
    return re.sub(r"[-+]?\d+(\.\d+)?", "#", why).strip()[:70]


def wait_summary(stats, signals=0):
    """Hourly 'why no signal' text from {market: {reason: count}}."""
    lines = [f"LAST HOUR: {signals} signal(s). Why the rest waited:"]
    for market, reasons in sorted(stats.items()):
        total = sum(reasons.values())
        top = sorted(reasons.items(), key=lambda kv: -kv[1])[:3]
        lines.append(f"  {market} ({total} scans): " + "; ".join(f"{n}x {r}" for r, n in top))
    return "\n".join(lines)


OFFSETS_FILE = os.environ.get("OFFSETS_FILE", "offsets.json")


def offsets_file_stamp():
    try:
        return os.path.getmtime(OFFSETS_FILE)
    except OSError:
        return None


def load_offsets(env, names=None):
    """{symbol: offset} = broker price - feed price. offsets.json (written by calc_offset.py) is read
    first, then an OFFSET_<SYMBOL> environment variable overrides it."""
    out = {}
    try:
        with open(OFFSETS_FILE) as f:
            for k, v in json.load(f).items():
                out[k.upper()] = float(v)
    except (OSError, ValueError, TypeError, AttributeError):
        out = {}
    for n in (names if names is not None else INSTRUMENTS):
        raw = env.get(f"OFFSET_{n}")
        if raw:
            try:
                out[n] = float(raw)
            except ValueError:
                raise SystemExit(f"OFFSET_{n} must be a number, got {raw!r}")
    return {k: v for k, v in out.items() if names is None or k in names}


def selective_scan(name, now_ts, scfg, store, offsets=None, spreads=None, specs=None, lang="en",
                   news=None, news_block=False, balance=0.0, risk_percent=0.5, contracts=None, news_status=None):
    """Runs the selective engine for one market.
    Returns (status_text, alert_or_None, lagging). lagging=True means the M5 candle that just
    closed is not listed by the feed yet, so the caller should try again in a minute."""
    market = markets.canonical(name)
    if market not in markets.DEFAULTS:
        return f"{name}: the selective engine covers XAUUSD, NAS100, US30 and BTCUSD only", None, False
    inst = INSTRUMENTS[name]
    if market in US_INDEX_MARKETS and str(inst["id"]).startswith("^") and os.environ.get("US_HOURS_ONLY", "1") != "0":
        closed = extras.us_cash_closed(now_ts)
        if closed:   # the free feed is only live in the US cash session: do not scan stale candles
            return f"{name}: MARKET CLOSED (US session 09:30-16:00 New York, {closed})", None, False
    if market in US_INDEX_MARKETS:
        out_win = ny_session.us_window_closed(now_ts)
        if out_win:   # US100 / US30: a review only inside the New York open window (US_MODE=0 turns this off)
            rejections.log(market, None, "us_window", [out_win], now=now_ts)
            return f"{name}: WAIT (US mode) - {out_win}", None, False
    if market == "DAX40" and str(inst["id"]).startswith("^") and os.environ.get("DAX_HOURS_ONLY", "1") != "0":
        closed = extras.dax_cash_closed(now_ts)
        if closed:   # the free feed only moves in the Xetra session: do not scan stale candles
            return f"{name}: MARKET CLOSED (Xetra 09:00-17:30 Frankfurt, {closed})", None, False
    offset = (offsets or {}).get(name, 0.0)
    data = {}
    try:
        for tf, key in SELECTIVE_TFS:
            candles = get_candles(inst, tf)
            if offset:
                candles = [dict(c, open=c["open"] + offset, high=c["high"] + offset,
                                low=c["low"] + offset, close=c["close"] + offset) for c in candles]
            data[key] = candles
        connected = True
    except Exception as e:
        connected = False
        log.warning("selective: %s data error: %s", name, e)
    if connected:
        for tf, key in OPTIONAL_TFS:
            try:
                candles = get_candles(inst, tf)
                if offset:
                    candles = [dict(c, open=c["open"] + offset, high=c["high"] + offset,
                                    low=c["low"] + offset, close=c["close"] + offset) for c in candles]
                data[key] = candles
            except Exception as e:
                log.info("selective: optional %s data for %s unavailable: %s", tf, name, e)
    status = selective.data_status(inst["source"], data.get("m5"), 300, now_ts, connected)
    if status != "LIVE":
        rejections.log(market, None, "BLOCKED", [f"data {status}"], now_ts)
        return f"{name}: SELECTIVE WAIT (data {status})", None, False
    m5_closed = selective.closed_only(data["m5"], 300, now_ts)
    if m5_closed and int(m5_closed[-1]["time"].timestamp()) + 300 < now_ts - now_ts % 300:
        return f"{name}: waiting for the latest M5 candle to be listed", None, True
    for tf, key in SELECTIVE_TFS:  # same data-quality gate as the other engines
        quality = data_layer.check_candles(data[key], tf, now_ts, inst["source"] == "binance", min_bars=1)
        if quality.blocked:
            rejections.log(market, None, "BLOCKED", [f"data quality {tf}: {quality.reason}"], now_ts)
            return f"{name}: SELECTIVE WAIT (data quality {tf}: {quality.reason})", None, False
    if news_status and news_status[0] in ("unavailable", "stale") and os.environ.get("NEWS_STRICT") == "1":
        rejections.log(market, None, "BLOCKED", [news_status[1]], now_ts)
        return f"{name}: SELECTIVE WAIT ({news_status[1]})", None, False        # NEWS_STRICT=1: no calendar, no signal
    if news_block and extras.near_news(news or [], name, now_ts):
        near = extras.near_news(news, name, now_ts)[0]
        rejections.log(market, None, "BLOCKED", [f"high-impact news: {near['currency']} {near['title']}"], now_ts)
        return f"{name}: SELECTIVE WAIT (high-impact news: {near['currency']} {near['title']})", None, False
    spec = (specs or {}).get(market)
    spread = (spreads or {}).get(name)
    if spread is None and spec is not None:
        spread = spec.spread
    commission_price = None
    if spec is not None and spec.commission_per_lot is not None and spec.tick_size and spec.tick_value:
        commission_price = spec.commission_per_lot / (spec.tick_value / spec.tick_size)
    res = None
    cfgs = list(scfg) if isinstance(scfg, (list, tuple)) else [scfg]
    # each pair only uses the strategies that suit it (PAIR_STRATEGIES=0 turns the map off); the forex profile
    # is only for forex pairs, and a forex pair uses it whenever it is listed
    fx_pair = market in markets.FOREX
    allowed = strategies_for(market, name)
    if allowed is None:
        matching = [c for c in cfgs if c.forex == fx_pair]
    else:
        matching = [c for c in cfgs if c.profile in allowed and (fx_pair or not c.forex)]
    if not matching:
        matching = [c for c in cfgs if c.forex == fx_pair]
    candidates = ranked_candidates(market, data, now_ts, matching or cfgs, spread, commission_price, status,
                                   structure.calendar_for(inst["source"]))
    agree = candidates[1]
    for cfg in candidates[0]:
        # several profiles at once: the best scoring one that passes gives the signal (ids are shared, so
        # the same setup is never sent twice); when all wait, the first profile's reason is shown
        r = selective.process(market, data, now_ts, store, cfg, spread, commission_price, status,
                              calendar=structure.calendar_for(inst["source"]))
        if res is None or r["action"] != "WAIT":
            res = r
        if r["action"] != "WAIT":
            break
    d = inst["digits"]
    if res["action"] == "WAIT":
        line = wait_line(name, res)
        ny = ny_view(data, now_ts)
        if ny and ny_session.summary(ny):
            line += f" [{ny_session.summary(ny)}, {ny['phase']}]"
        mstate = state_view(data, now_ts, structure.calendar_for(inst["source"]))
        if mstate:
            line += f" [state: {mstate}]"
        if os.environ.get("ALERT_CLASSES", "1") != "0":
            cfg0 = candidates[0][0]

            def gate(plan, _cfg=cfg0):
                ok, why = flip_ifvg.cost_check(plan["risk"], plan["reward"], spread, commission_price, _cfg)
                if not ok:
                    return False, why
                if store is None:
                    return False, "no signal store"
                return store.open(market, plan["setup"], now_ts)               # one open signal per market, no opposite side

            ifvg_sigs = []
            line += track_setups(market, name, data, now_ts, cfg0, structure.calendar_for(inst["source"]), lang,
                                 store, res.get("reasons"), inst["digits"], gate, ifvg_sigs)
            if ifvg_sigs:                                                       # an IFVG setup passed every gate: it is the signal
                a = ifvg_sigs[0]
                a["setup"] = {k: (round(v, d) if isinstance(v, float) else v) for k, v in a["setup"].items()}
                if os.environ.get("ALERT_CLASSES", "1") != "0":
                    alert_state().mark_confirmed(market, a["setup"]["side"], now_ts)
                return f"{name}: SELECTIVE {a['setup']['side'].upper()} ({a.get('tag', 'IFVG')})", a, False
        elif os.environ.get("ZONE_WATCH", "1") != "0":
            for zw in selective.zone_watch(market, data, now_ts, candidates[0][0], structure.calendar_for(inst["source"]), lang):
                if _ZONE_SEEN.get(zw["id"]) is None:       # once per zone (a restart may show it once more)
                    _ZONE_SEEN[zw["id"]] = now_ts
                    _save_zone_seen(_ZONE_SEEN)
                    line += "\n" + zw["text"]
        if os.environ.get("VERBOSE") == "1":  # full report: HTF, liquidity, structure, zones, retest, M5 candle
            line += "\n" + selective.explain(res)
        return line, None, False
    setup = {k: (round(v, d) if isinstance(v, float) else v) for k, v in res["setup"].items()}
    if os.environ.get("ALERT_CLASSES", "1") != "0":
        setup["stage_trail"] = reversal_machine.mark_final(alert_state(), market, setup["side"], now_ts)
        alert_state().mark_confirmed(market, setup["side"], now_ts)     # CONFIRMED: the watch setups of this side are finished
    if os.environ.get("SIGNAL_FORMAT", "kenan") == "classic":
        fmt = selective.format_signal_ar if lang == "ar" else selective.format_signal
        text = fmt(name, dict(res, setup=setup), d)
    else:
        near = extras.near_news(news or [], name, now_ts) if news else []
        if news_status and news_status[0] in ("unavailable", "stale"):
            news_text = news_status[1]
        elif news_status and news_status[0] == "off":
            news_text = news_status[1]
        elif near:
            news_text = "BLOCKED: " + "; ".join(f"{e['currency']} {e['title']}" for e in near)
        else:
            news_text = "clear (no high-impact event near)"
        ttl_h = float(os.environ.get("SIGNAL_TTL_HOURS") or (72 if setup.get("profile") == "swing" else 6))
        ny = ny_view(data, now_ts)
        ny_note = (" | " + ny_session.summary(ny)) if ny and ny_session.summary(ny) else ""
        mstate = state_view(data, now_ts, structure.calendar_for(inst["source"]))
        ny_note += f" | MARKET STATE: {mstate}" if mstate else ""
        text = selective.format_kenan(name, dict(res, setup=setup), d, news_text, extras.session_text(now_ts) + ny_note, ttl_h)
        text += "\n" + "\n".join(selective.entry_checklist(setup, res, ar=(lang == "ar")))
    if (setup.get("volume") or {}).get("text") and os.environ.get("SIGNAL_FORMAT", "kenan") == "classic":
        text += "\nDisplacement volume: " + setup["volume"]["text"]
    if len(agree) > 1:
        text += (f"\nStrategies agreeing on this side: {len(agree)} ({', '.join(x.upper() for x in agree)}) | "
                 f"توافق {len(agree)} استراتيجيات")
    warn = fallback_note(inst)
    if warn:
        text += "\n" + warn
    sid_reg = res.get("id")
    if sid_reg:
        tps = [setup.get(k) for k in ("tp1", "tp", "tp3") if setup.get(k) is not None]
        risk0 = abs(setup["entry"] - setup["sl"]) or 1
        flip_ifvg._pending(alert_state(), sid_reg, market, setup["side"], now_ts, lang, entry=setup["entry"], sl=setup["sl"], tps=tps,
                           rr=f"{abs(setup['tp'] - setup['entry']) / risk0:.1f}", invalid=setup.get("invalidation", ""),
                           wait="this signal is already confirmed: if you did not enter, do not chase it once price is more than 0.5 ATR "
                                "from the entry; cancel it if the invalidation level is closed beyond")
        alert_state().save()
        text += "\n" + decisions.options_text(sid_reg, lang)
    lot = extras.lot_text(balance, risk_percent, setup, spec, (contracts or {}).get(name)).strip()
    if lot:
        text += "\n" + lot
    try:   # the dollar index as context for gold / EURUSD / BTC (never blocks the signal)
        import report_tools
        if market in report_tools.USD_INVERSE and os.environ.get("USD_MONITOR", "1") != "0":
            usd_text = report_tools.dollar_signal_line(market, setup["side"], report_tools.dollar_state(now_ts))
            if usd_text:
                text += "\n" + usd_text
    except Exception as e:
        log.warning("dollar monitor failed: %s", e)
    return f"{name}: SELECTIVE {res['action']}", {"text": text, "setup": setup, "market": market, "decision_id": sid_reg,
                                                  "id": res.get("id"), "bar_time": setup["bar_time"]}, False


# ===== Notifications =====

SOUND_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alert.wav")


def sound_path():
    """Custom alert sound: the ALERT_SOUND file, or alert.wav next to this script.
    ALERT_SOUND=off turns the custom sound off. Returns None when there is nothing to play."""
    choice = os.environ.get("ALERT_SOUND")
    if choice and choice.lower() == "off":
        return None
    path = os.path.expanduser(choice) if choice else SOUND_FILE
    return path if os.path.exists(path) else None


ENTRY_KINDS = ("ict", "classic", "rct")  # alerts that are a ready entry; all others stay silent


SIDE_STYLE = {"buy": ("\U0001F535", "\033[1;94m", "0066FF"),    # blue
              "sell": ("\U0001F534", "\033[1;91m", "FF0000")}   # red


# management / result messages, coloured by the action wanted (side colours stay for entries)
ACTION_STYLE = (("US MARKET OPEN", ("\U0001F7E2", "\033[1;92m", "00CC44")),
                ("CLOSE NOW", ("\U0001F534", "\033[1;97;41m", "FF0000")),         # red band: close it
                ("REVERSAL WARNING", ("\U0001F7E1", "\033[1;93m", "FFCC00")),     # yellow: move the stop
                ("TRADE +", ("\U0001F7E2", "\033[1;92m", "00CC44")),              # green: in profit, protect it
                ("RESULT: WIN", ("\U0001F7E2", "\033[1;92m", "00CC44")),
                ("RESULT: LOSS", ("\U0001F534", "\033[1;91m", "FF0000")))


def style_for(text, side=None):
    """(emoji, ansi colour, led colour) for a message: the action prefix wins, then the side."""
    for prefix, style in ACTION_STYLE:
        if text.startswith(prefix):
            return style
    return SIDE_STYLE.get(side, ("", "", ""))


REVIEW_FILE = os.environ.get("REVIEW_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                            "last_signal_review.txt")
REVIEW_HEADER = (
    "حلّل هذه الإشارة كمراجع مستقل قبل أن أدخل: هل تتوافق مع قواعد ICT/SMC المذكورة؟ ما نقاط الضعف "
    "والمخاطر (التكلفة، جودة البيانات، الأخبار، الوقت)؟ وما الذي أتحقق منه في MT5 قبل الدخول؟ "
    "لا تعدني بربح ولا تتنبأ بالسعر.\n"
    "Review this signal as an independent second opinion before I enter. Does it match the ICT/SMC "
    "rules it states? Weak points and risks (cost, data quality, news, time)? What should I verify in "
    "MT5 first? Do not promise profit or predict price.\n\n")


def save_for_review(text, path=None):
    """Writes the signal with a review request to a file and copies it to the phone clipboard
    (termux-clipboard-set) so it can be pasted to an AI assistant. REVIEW_COPY=0 turns it off.
    Never raises: a problem here must not stop the alerts."""
    if os.environ.get("REVIEW_COPY", "1") == "0":
        return None
    pack = REVIEW_HEADER + text
    try:
        with open(path or REVIEW_FILE, "w") as f:
            f.write(pack)
    except OSError:
        log.exception("could not save the review file")
    if shutil.which("termux-clipboard-set"):
        try:
            subprocess.run(["termux-clipboard-set"], input=pack.encode(), timeout=10)
        except Exception:
            log.exception("termux-clipboard-set failed")
    return pack


def notify(text, entry=False, side=None, alert=False, decision_id=None):
    """Shows the message. Only entry=True (a trade ready to enter) makes a sound; everything
    else (sessions, news, results, heads-up events) is only printed on the Termux screen
    (INFO_NOTIFY=1 also shows it as a silent phone notification).
    side 'buy' / 'sell' colours the alert: blue for BUY, red for SELL (terminal colour, a coloured
    circle in the notification and Telegram text, and the phone's notification light).
    alert=True (trade management: move the stop, reinforcement) shows a phone notification and plays
    the same tone as an entry (MANAGE_SOUND=0 makes it silent); plain info messages stay silent."""
    emoji, ansi, led = style_for(text, side)
    print(f"{ansi}{text}\033[0m" if ansi else text, flush=True)
    if not entry and not alert and os.environ.get("INFO_NOTIFY") != "1":
        return  # info messages stay in the Termux screen only: no phone notification, no sound
    if emoji:
        text = f"{emoji} {text}"
    loud = entry or (alert and os.environ.get("MANAGE_SOUND", "1") != "0")
    sound = sound_path() if loud and shutil.which("termux-media-player") else None
    if shutil.which("termux-notification"):
        arabic = alert_classes.arabic()
        title = ("ICT SMC" if entry else "ICT SMC (manage)" if alert else "ICT SMC (info)") if not arabic else \
                ("إشارة دخول" if entry else "إدارة الصفقة" if alert else "معلومة")
        if side:
            title = f"{emoji} {side.upper()} - ICT SMC"
        elif emoji:
            title = f"{emoji} {text.split(chr(10))[0].split(':')[0].replace(emoji + ' ', '')}"
        cmd = ["termux-notification", "--title", title,
               "--content", text, "--priority", "high" if entry or alert else "low"]
        if led:
            cmd += ["--led-color", led]
        path = alert_classes.save_full_text("NOTIFY", text) if arabic else ""
        if path:
            cmd += ["--action", f"termux-open {path}"]                 # tap = open the full text
        cmd += decisions.button_args(decision_id)                      # ENTER / WAIT / IGNORE buttons (you decide, the bot never trades)
        if loud and not sound:
            cmd.append("--sound")  # no custom sound: use the phone's notification sound
        try:
            subprocess.run(cmd, timeout=10)
        except Exception:
            log.exception("termux-notification failed")
    if sound:
        try:
            subprocess.run(["termux-media-player", "play", sound], timeout=10)
        except Exception:
            log.exception("termux-media-player failed")
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            fields = {"chat_id": chat, "text": text}
            if not (entry or alert):
                fields["disable_notification"] = "true"  # no sound on the Telegram side either
            body = urllib.parse.urlencode(fields).encode()
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", data=body, timeout=20)
        except Exception:
            log.exception("Telegram send failed")


# ===== Main loop =====

def restore_tracking(store, track, names):
    """After a restart, follow every saved ACTIVE signal again so its TP / SL still releases the
    market lock. Returns the markets that are NOT followed (their feed is not being run): those
    locks stay until the store's TTL expires them."""
    track.on_done = lambda it, outcome: store.close(it["ref"], outcome) if it.get("ref") else None
    unfollowed = []
    for sid, sig in list(store.active_signals.items()):
        market = sig.get("market", sid)
        feed = markets.feed_name(market)
        if feed in names:
            track.add(feed, "5m", "selective", sig, sig["bar_time"], ref=sid, market=True)
        elif market not in unfollowed:
            unfollowed.append(market)
    return unfollowed


def parse_killzones(*specs):
    """'7-11' style UTC hour windows -> ((7.0, 11.0), ...). Bad text stops the bot with a clear message."""
    out = []
    for sp in specs:
        try:
            a, b = (float(x) for x in sp.split("-"))
        except ValueError:
            raise SystemExit(f"killzone must look like 7-11 (UTC hours), got {sp!r}")
        out.append((a, b))
    return tuple(out)


# Strategies that suit each market (the engine checks all of them and the best scoring one that passes gives the
# signal). This is a rule-based selector, not a trained model. PAIR_STRATEGIES=0 uses every profile on every pair.
PAIR_STRATEGIES = {
    "XAUUSD": ("scalp", "swing", "golden", "active", "strict"),
    "BTCUSD": ("scalp", "swing", "active", "strict"),
    "NAS100": ("scalp", "swing", "active", "strict"), "US100": ("scalp", "swing", "active", "strict"),
    "US30": ("scalp", "swing", "active", "strict"), "DAX40": ("scalp", "swing", "active", "strict"),
    "EURUSD": ("forex", "swing", "scalp"), "GBPJPY": ("forex", "swing", "scalp"), "JPYGBP": ("forex", "swing", "scalp"),
    "GBPUSD": ("forex", "swing", "scalp"), "USDJPY": ("forex", "swing", "scalp"),
}


def strategies_for(market, name=None):
    """Profile names allowed for a market, or None (= all) when the map is off or the market is unknown."""
    if os.environ.get("PAIR_STRATEGIES", "1") == "0":
        return None
    return PAIR_STRATEGIES.get(market) or PAIR_STRATEGIES.get(name)


def ranked_candidates(market, data, now_ts, cfgs, spread, commission_price, status, calendar):
    """(ordered cfgs, names that pass): every candidate profile is evaluated WITHOUT taking a lock; the ones that
    pass come first, best score first, so the strongest setup is the one issued. Nothing passes: original order."""
    if len(cfgs) < 2:
        return list(cfgs), []
    passing = []
    for i, cfg in enumerate(cfgs):
        try:
            r = selective.evaluate(market, data, now_ts, cfg, spread, commission_price, status, calendar)
        except Exception as e:       # a profile problem must never stop the other profiles
            log.warning("selective: %s profile check failed: %s", cfg.profile, e)
            continue
        if r["action"] != "WAIT":
            passing.append((-r.get("score100", 0), i, cfg))
    passing.sort(key=lambda t: (t[0], t[1]))
    order = [c for _, _, c in passing] + [c for c in cfgs if c not in [p[2] for p in passing]]
    return order, [c.profile for _, _, c in passing]


# SIGNAL_LEVEL presets: how many signals you accept. "strict" is the approved default. The net R:R floor (1.5, MIN_NET_RR),
# the mandatory sweep/MSS/FVG logic, the news filter and the duplicate protection are NEVER relaxed by a preset.
# An explicit setting (MIN_DISPLACEMENT=..., VOLUME_MULT=..., READY_SCORE=...) always beats the preset.
SIGNAL_LEVELS = {
    "balanced": dict(min_displacement=1.2, vol_mult=1.3, body_mult=1.1, late_entry_frac=0.6, killzones=((7.0, 17.0),),
                     ready={"scalp": 82, "swing": 78, "other": 80}),
    "active": dict(min_displacement=1.0, vol_mult=1.0, body_mult=1.0, late_entry_frac=0.0, killzones=(),
                   ready={"scalp": 80, "swing": 76, "other": 78}),
}


def apply_signal_level(over, env, profile, two_modes):
    """Applies SIGNAL_LEVEL (strict | balanced | active) to the config overrides `over` (changed in place)."""
    level = (env.get("SIGNAL_LEVEL") or "strict").strip().lower()
    if level == "strict":
        return
    if level not in SIGNAL_LEVELS:
        raise SystemExit(f"SIGNAL_LEVEL must be strict, balanced or active, got {level!r}")
    p = SIGNAL_LEVELS[level]
    for key in ("min_displacement", "vol_mult", "body_mult", "late_entry_frac"):
        env_name = {"min_displacement": "MIN_DISPLACEMENT", "vol_mult": "VOLUME_MULT", "body_mult": "BODY_MULT",
                    "late_entry_frac": "LATE_ENTRY_FRAC"}[key]
        if key == "late_entry_frac" and not two_modes:
            continue                                   # the late-entry rule only belongs to scalp and swing
        if not env.get(env_name):
            over[key] = p[key]
    if (env.get("KILLZONES", "1") != "0" and profile != "swing"        # swing runs all day by design
            and not env.get("KILLZONE_LONDON") and not env.get("KILLZONE_NY")):
        over["killzones"] = p["killzones"]
    if not env.get("READY_SCORE"):
        over["ready_score"] = p["ready"].get(profile, p["ready"]["other"])
    if level == "active" and not env.get("M15_CONFIRM"):
        over["require_m15_confirm"] = False          # the most active level triggers on the M5 candle alone


def build_selective_config(env):
    """SelectiveConfig from environment variables. The profile (strict / active / loose) sets its own
    defaults; a variable only overrides them when it is actually set (an unset READY_SCORE must not
    quietly replace the profile's score threshold)."""
    def num(var, cast=float):
        raw = env.get(var)
        if not raw:
            return None
        try:
            return cast(float(raw))
        except ValueError:
            raise SystemExit(f"{var} must be a number, got {raw!r}")

    profile = env.get("PROFILE", "strict").lower()
    if profile not in selective.PROFILES:
        raise SystemExit("PROFILE must be strict, active, loose, scalp, swing, golden or forex")
    over = {"min_confluence": num("MIN_CONFLUENCE", int), "slippage": num("SLIPPAGE"),
            "max_daily_losses": num("MAX_DAILY_LOSSES", int), "max_daily_loss_pct": num("MAX_DAILY_LOSS_PCT"),
            "max_daily_loss_r": num("MAX_DAILY_LOSS_R"), "risk_percent": num("RISK_PERCENT"),
            "ready_score": num("READY_SCORE", int)}
    over = {k: v for k, v in over.items() if v is not None}
    # --- tightened filtering (all on by default; each has an env switch to relax it)
    notify = int(float(env.get("MIN_NOTIFY_SCORE") or 70))          # phone alert only from this score; below = WATCH
    base_cfg = selective.make_config(profile)
    two_modes = profile in ("scalp", "swing")                       # these two keep their own score gate (85 / 80)
    if "ready_score" not in over and not two_modes:                 # an explicit READY_SCORE always wins
        over["ready_score"] = notify or base_cfg.ready_score         # default 70 (MIN_NOTIFY_SCORE; 0 = the profile's own)
    over["watch_score"] = min(base_cfg.watch_score, 75 if two_modes or not notify else max(notify - 5, 50))
    htf = (env.get("HTF_MODE") or "h1m15").strip().lower()          # the general frame is H1 (swing keeps D1 + H4)
    if htf not in ("h1m15", "default"):
        raise SystemExit("HTF_MODE must be h1m15 or default")
    if profile != "swing":
        over["htf_mode"] = htf
    over["spread_margin"] = float(env.get("SPREAD_MARGIN") or 1.25)
    if not two_modes:
        over["require_mss_fvg"] = env.get("REQUIRE_MSS_FVG", "1") != "0"
    over["counter_trend_min_score"] = int(float(env.get("COUNTER_TREND_MIN_SCORE") or 70))
    floor = float(env.get("MIN_NET_RR") or 1.5)
    over["min_net_rr"] = max(base_cfg.min_net_rr, floor)
    if env.get("KILLZONES", "1") == "0":
        over["killzones"] = ()
        over["blackout"] = ()
    elif not two_modes:
        over["killzones"] = parse_killzones(env.get("KILLZONE_LONDON", "7-11"), env.get("KILLZONE_NY", "12-16"))
    elif profile == "scalp" and (env.get("KILLZONE_LONDON") or env.get("KILLZONE_NY")):
        over["killzones"] = parse_killzones(env.get("KILLZONE_LONDON", "8-11"), env.get("KILLZONE_NY", "13-16.5"))
    over["volume_filter"] = env.get("VOLUME_FILTER", "1") != "0"
    over["require_m15_confirm"] = env.get("M15_CONFIRM", "1") != "0"      # a closed M15 candle must confirm (M15_CONFIRM=0 = off)
    if env.get("VOLUME_MULT"):
        over["vol_mult"] = float(env["VOLUME_MULT"])
    if env.get("BODY_MULT"):
        over["body_mult"] = float(env["BODY_MULT"])
    over["volume_required"] = env.get("VOLUME_REQUIRED", "0") == "1"
    apply_signal_level(over, env, profile, two_modes)
    # optional relaxations of the two modes (defaults keep the strict rules)
    if env.get("MIN_DISPLACEMENT"):
        over["min_displacement"] = float(env["MIN_DISPLACEMENT"])
    if env.get("LATE_ENTRY_FRAC"):
        over["late_entry_frac"] = float(env["LATE_ENTRY_FRAC"])          # 0 = never cancel a late entry
    if env.get("STRICT_SEQUENCE") in ("0", "1"):
        over["strict_sequence"] = env["STRICT_SEQUENCE"] == "1"
    if env.get("REQUIRE_FIB") in ("0", "1"):
        over["require_fib"] = env["REQUIRE_FIB"] == "1"
    if env.get("REQUIRE_SWEEP") in ("0", "1"):
        over["require_sweep"] = env["REQUIRE_SWEEP"] == "1"
    # no verified spread for a market: still give the signal, labelled "COST UNVERIFIED" (ALLOW_UNKNOWN_COST=0
    # makes the cost check block instead). Fill markets.json / SPREAD_<SYMBOL> to replace the unknown.
    over["allow_unknown_cost"] = env.get("ALLOW_UNKNOWN_COST", "1") != "0"
    return selective.make_config(profile, **over)


ALL_PROFILES = ("strict", "swing", "golden", "active", "scalp", "forex")   # PROFILE=all: most selective first


def build_selective_configs(env):
    """PROFILE may list several profiles ("scalp,swing") or be "all": every profile is checked on each
    scan and the first one that passes gives the signal. Returns a list of SelectiveConfig."""
    names = env.get("PROFILE", "strict").lower()
    names = list(ALL_PROFILES) if names == "all" else [n.strip() for n in names.split(",") if n.strip()]
    return [build_selective_config(dict(env, PROFILE=n)) for n in names] or [build_selective_config(env)]


def describe_config(scfg, engine):
    if isinstance(scfg, (list, tuple)):
        if len(scfg) > 1:
            first = scfg[0]
            return (f"Engine: {engine} | profiles {'+'.join(c.profile.upper() for c in scfg)} (first that passes "
                    f"gives the signal) | risk {first.risk_percent}%/trade, stop after "
                    f"{first.max_daily_losses} losses or {first.max_daily_loss_pct}% a day")
        scfg = scfg[0]
    """One startup line that states which rules are really active (profile, score gate, risk limits)."""
    if engine == "legacy":
        return "Engine: legacy"
    return (f"Engine: selective | profile {scfg.profile.upper()} | signal needs score {scfg.ready_score}+/100 "
            f"and {scfg.min_confluence}/8 checks | risk {scfg.risk_percent}%/trade, stop after "
            f"{scfg.max_daily_losses} losses or {scfg.max_daily_loss_pct}% a day"
            + (" | cost UNVERIFIED allowed" if scfg.allow_unknown_cost else ""))


def run():
    names = [markets.feed_name(n) for n in os.environ.get(
        "SYMBOLS", "BTCUSD,XAUUSD,US100,US30,EURUSD,GBPJPY,GBPUSD,USDJPY").split(",") if n.strip()]
    unknown = [n for n in names if n not in INSTRUMENTS]
    if unknown:
        raise SystemExit(f"Unknown symbols: {unknown}. Choose from {list(INSTRUMENTS)}")

    offsets = load_offsets(os.environ, names)
    offsets_stamp = offsets_file_stamp()

    def number(var, default):
        raw = os.environ.get(var)
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            raise SystemExit(f"{var} must be a number, got {raw!r}")

    cfg = Config(min_score=int(os.environ.get("MIN_SCORE", "0")),
                 classic_enabled=os.environ.get("CLASSIC", "1") != "0",
                 risk_percent=number("RISK_PERCENT", 0.25))
    opts = {"balance": number("BALANCE", 0.0),
            "contracts": {n: number(f"CONTRACT_{n}", None) for n in names
                          if os.environ.get(f"CONTRACT_{n}")},
            "markets": markets.load_markets(),
            "session_only": os.environ.get("SESSION_ONLY") == "1",
            "news_block": os.environ.get("NEWS_BLOCK") == "1",
            "news": [],
            "us_open_only": os.environ.get("US_OPEN_ONLY") == "1",
            "events": tuple(k for k in os.environ.get("EVENTS", "sweep,fvg,ob").lower().split(",")
                            if k in ("sweep", "fvg", "ob"))}
    default_tfs = ("5m,15m,1h,4h" if os.environ.get("ENGINE", "selective").lower() == "selective"
                   else "1m,5m,15m,1h,4h,1d")  # fewer downloads when only the selective engine is on
    tfs = [t.strip().lower() for t in os.environ.get("TIMEFRAMES", default_tfs).split(",") if t.strip()]
    if "5m" not in tfs:
        tfs.append("5m")  # the result tracker (which releases the signal locks) follows M5 candles
    bad = [t for t in tfs if t not in TF_SECONDS]
    if bad:
        raise SystemExit(f"Unknown timeframes: {bad}. Choose from {list(TF_SECONDS)}")
    event_tfs = {t.strip().lower() for t in os.environ.get(
        "EVENT_TIMEFRAMES", "15m,1h,4h,1d").split(",") if t.strip()}
    cfg.rct_enabled = os.environ.get("RCT", "1") != "0"
    engine = os.environ.get("ENGINE", "selective").lower()
    if engine not in ("selective", "legacy", "both"):
        raise SystemExit("ENGINE must be selective, legacy or both")
    use_selective = engine in ("selective", "both")
    alert_lang = os.environ.get("ALERT_LANG", "en").lower()  # en (default) or ar (Arabic alerts)
    opts["news_block"] = os.environ.get("NEWS_BLOCK", "1" if use_selective else "0") == "1"
    opts["legacy_ict"] = engine in ("legacy", "both")
    if engine == "selective":  # the old classic / RCT alerts are only for ENGINE=legacy or both
        cfg.classic_enabled = False
        cfg.rct_enabled = False
    scfg = build_selective_configs(os.environ)
    spreads = {n: number(f"SPREAD_{n}", None) for n in names if os.environ.get(f"SPREAD_{n}")}
    # MAX_ACTIVE signals may be open on one market at once (1 = old one-signal lock);
    # ALLOW_OPPOSITE=0 keeps a BUY and a SELL from being open together on the same market.
    # an open signal expires after SIGNAL_TTL_HOURS (default 6, or 72 for the swing profile)
    ttl_hours = float(os.environ.get("SIGNAL_TTL_HOURS") or (72 if os.environ.get("PROFILE", "").lower() == "swing" else 6))
    store = signal_store.SignalStore(
        os.environ.get("STATE_FILE", signal_store.DEFAULT_FILE), ttl=ttl_hours * 3600,
        max_active=int(os.environ.get("MAX_ACTIVE") or 1),
        allow_opposite=os.environ.get("ALLOW_OPPOSITE", "0") != "0") if use_selective else None
    track = tracker.Tracker(os.environ.get("RESULTS_FILE", tracker.DEFAULT_CSV)) \
        if os.environ.get("TRACK", "1") != "0" else None
    last_bucket = {}
    if store and track:
        restore_tracking(store, track, names)
    if store and store.load_error:
        print(f"WARNING: signal state file was unreadable ({store.load_error}); started clean", flush=True)
    us_alerts = os.environ.get("US_OPEN_ALERTS", "1") != "0"
    session_alerts = os.environ.get("SESSION_ALERTS", "1") != "0"
    last_sessions = None
    use_news = os.environ.get("NEWS", "1") != "0"
    news_fetched = 0
    seen = set()
    print(f"ICT SMC alerts started for: {', '.join(names)} on {', '.join(tfs)}", flush=True)
    print(describe_config(scfg, engine), flush=True)
    if not structure.tz_ok():
        print("WARNING: no timezone database. New York sessions and killzones are one hour wrong in summer. "
              "Fix: pip install tzdata", flush=True)
    if offsets:
        print(f"Price offsets: {offsets}", flush=True)

    last_beat = 0
    wait_stats, hour_signals = {}, 0
    report_minutes = float(os.environ.get("REPORT_MINUTES") or 60)    # 0 = no periodic report
    trades_report_minutes = float(os.environ.get("TRADES_REPORT_MINUTES") or 30)   # open / closed trades and P/L per day; 0 = off
    last_trades_report = 0
    last_report = 0
    zones_report_minutes = float(os.environ.get("ZONES_REPORT_MINUTES") or 30)   # entry zones near price + quality; 0 = off
    last_zones_report = 0
    us_names = [n for n in names if markets.canonical(n) in US_INDEX_MARKETS and str(INSTRUMENTS[n]["id"]).startswith("^")]
    us_open = None
    while True:
        now_ts = int(time.time())
        if us_names and use_selective and os.environ.get("US_HOURS_ONLY", "1") != "0":
            us_open, us_msg = extras.us_session_event(us_open, now_ts, " / ".join(us_names))
            if us_msg:
                notify(us_msg, alert=us_msg.startswith("US MARKET OPEN"))
        if report_minutes and use_selective and now_ts - last_report >= report_minutes * 60:
            last_report = now_ts
            try:
                import report_tools
                print(report_tools.market_report(names, now_ts, wait_stats, offsets, hour_signals), flush=True)
            except Exception as e:   # a report must never stop the bot
                log.warning("market report failed: %s", e)
        if trades_report_minutes and now_ts - last_trades_report >= trades_report_minutes * 60:
            last_trades_report = now_ts
            try:
                import daily_report
                prices = {}
                for _n in names:
                    try:
                        prices[markets.canonical(_n)] = get_candles(INSTRUMENTS[_n], "5m")[-1]["close"] + offsets.get(_n, 0.0)
                    except Exception:                                   # a missing price only hides that market's open P/L
                        pass
                states = {it["ref"]: it["state"] for it in (track.items if track else []) if it.get("ref")}
                text = daily_report.build(store, now_ts, prices, states, track.tally() if track else None)
                print(text, flush=True)
                daily_report.notify(text)
            except Exception as e:                                      # a report must never stop the bot
                log.warning("trades report failed: %s", e)
        if zones_report_minutes and use_selective and now_ts - last_zones_report >= zones_report_minutes * 60:
            last_zones_report = now_ts
            try:
                import zones_report
                ztext = zones_report.build(now_ts)
                print(ztext, flush=True)
                zones_report.notify(ztext)
            except Exception as e:                                      # a report must never stop the bot
                log.warning("zones report failed: %s", e)
        if now_ts - last_beat >= 3600:
            if last_beat and use_selective:
                print(wait_summary(wait_stats, hour_signals), flush=True)
            wait_stats, hour_signals = {}, 0
            last_beat = now_ts
            print(f"[{datetime.fromtimestamp(now_ts, tz=timezone.utc):%H:%M UTC}] bot is running", flush=True)
        if use_news and now_ts - news_fetched > 3600:
            news_fetched = now_ts
            try:
                opts["news"] = extras.fetch_news()
                opts["news_ok_ts"], opts["news_error"] = now_ts, None
            except Exception as e:
                news_fetched = now_ts - 3000      # a failed download is retried in 10 minutes, not an hour
                opts["news_error"] = str(e)[:80]
                print(f"news calendar unavailable: {e}", flush=True)
        opts["news_status"] = extras.news_status(opts.get("news_ok_ts"), opts.get("news_error"), now_ts, use_news)
        if us_alerts:
            open_ts = extras.us_open_ts(now_ts)
            if open_ts is not None:
                mins = round((open_ts - now_ts) / 60)
                if 0 < mins <= 15 and ("us-pre", open_ts) not in seen:
                    seen.add(("us-pre", open_ts))
                    notify(f"US OPEN in {mins} min (09:30 New York). Expect fast moves. "
                           f"Check open orders and the news.")
                elif -3 <= mins <= 0 and ("us-open", open_ts) not in seen:
                    seen.add(("us-open", open_ts))
                    notify("US MARKET OPEN now. Volatility is usually highest in the first hour.")
        if session_alerts:
            now_sessions = extras.active_sessions(now_ts)
            if last_sessions is not None:
                for sess in now_sessions:
                    if sess not in last_sessions:
                        notify(f"SESSION OPEN: {sess} session has started. Expect more movement.")
            last_sessions = now_sessions
        for e in opts["news"]:
            key = ("news", e["ts"], e["title"])
            if 0 <= e["ts"] - now_ts <= 30 * 60 and key not in seen and \
                    any(e["currency"] in extras.CURRENCIES.get(n, {"USD"}) for n in names):
                seen.add(key)
                notify(f"NEWS in {round((e['ts'] - now_ts) / 60)} min: "
                       f"{e['currency']} {e['title']} (high impact). Be careful with open orders.")
        stamp = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%H:%M UTC")
        if offsets_file_stamp() != offsets_stamp:       # calc_offset.py saved a new value: use it without a restart
            offsets_stamp = offsets_file_stamp()
            offsets.clear()
            offsets.update(load_offsets(os.environ, names))
            print(f"Price offsets updated: {offsets}", flush=True)
        if use_selective and now_ts % 300 < 180:  # first 3 minutes of each M5 candle
            bucket = now_ts // 300
            for name in names:
                if last_bucket.get(("sel", name)) == bucket:
                    continue
                try:
                    status, alert, lagging = selective_scan(
                        name, now_ts, scfg, store, offsets, spreads, opts["markets"], alert_lang,
                        opts["news"], opts["news_block"], opts["balance"], cfg.risk_percent,
                        opts["contracts"], opts.get("news_status"))
                    if not lagging:
                        last_bucket[("sel", name)] = bucket
                    print(f"[{stamp} sel] {status}", flush=True)
                    if not alert and not lagging:
                        key = reason_key(status)
                        wait_stats.setdefault(name, {})[key] = wait_stats.setdefault(name, {}).get(key, 0) + 1
                    if alert:
                        hour_signals += 1
                        notify(alert["text"], entry=True, side=alert["setup"]["side"], decision_id=alert.get("decision_id"))
                        save_for_review(alert["text"])
                        if track:
                            track.add(name, "5m", "selective", alert["setup"], alert["bar_time"],
                                      ref=alert["id"], market=alert["setup"].get("market_entry", True))
                except Exception as e:
                    print(f"[{stamp} sel] {name}: error: {e}", flush=True)
        for tf in tfs:
            bucket = now_ts // TF_SECONDS[tf]
            if last_bucket.get(tf) == bucket:
                continue  # this timeframe already ran for its current candle
            last_bucket[tf] = bucket
            tf_opts = dict(opts, events=opts["events"] if tf in event_tfs else ())
            for name in names:
                try:
                    status, alerts = analyze(name, cfg, now_ts, seen, offsets, tf_opts, tf)
                    if tf != "1m" or alerts:
                        print(f"[{stamp} {tf}] {status}", flush=True)
                    for alert in alerts:
                        notify(alert["text"], entry=alert["kind"] in ENTRY_KINDS,
                               side=(alert["setup"] or {}).get("side") if alert["kind"] in ENTRY_KINDS else None)
                        if track and alert["setup"]:
                            track.add(name, tf, alert["kind"], alert["setup"], alert["bar_time"])
                    if track and (name, tf) in LAST_R:
                        for note in track.update(name, tf, LAST_R[(name, tf)]):
                            notify(note, alert=note.startswith(tracker.MANAGE_PREFIXES))
                except Exception as e:
                    print(f"[{stamp} {tf}] {name}: error: {e}", flush=True)
        # wake up shortly after the next minute starts
        time.sleep(max(1, 60 - time.time() % 60 + 8))


def supervise():
    """Keeps the bot alive: an unexpected crash is printed and the bot restarts after 30 seconds.
    Ctrl+C and bad settings (SystemExit) still stop it."""
    while True:
        try:
            run()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            print(f"BOT CRASHED: {type(e).__name__}: {e}. Restarting in 30 s.", flush=True)
            time.sleep(30)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    supervise()
