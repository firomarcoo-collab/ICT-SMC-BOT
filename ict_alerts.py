"""ICT SMC alerts: same signal rules as the EA, but it only sends alerts (no orders).

Runs on Termux with the Python standard library only (no pip install needed).
Prices come from free public sources, so they can differ from your broker's prices.

Environment variables (all optional):
    SYMBOLS           comma list, default BTCUSD,XAUUSD,US100,US30
    OFFSET_<SYMBOL>   e.g. OFFSET_XAUUSD=-12.5, added to feed prices (broker price - feed price)
    MIN_SCORE         0-4: ICT alerts need at least this many classical confirmations (default 0)
    FUTURES           1 = futures for gold/indices instead of spot; or a list: FUTURES=US100,US30
    BALANCE           your account balance in $: adds the lot size to each alert
    RISK_PERCENT      risk per trade in % (default 0.5), used for the lot size
    CONTRACT_<SYMBOL> your own assumption of units per 1 lot (only used when markets.json is not filled)
    SESSION_ONLY      1 = no alerts outside London / New York hours
    NEWS              0 = turn off the high-impact news calendar (default on)
    NEWS_BLOCK        1 = no alerts within 30 min before / 15 min after high-impact news
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
    TELEGRAM_TOKEN    Telegram bot token (to also get alerts in Telegram)
    TELEGRAM_CHAT_ID  your Telegram chat id
"""
import json
import logging
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

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
}
FUTURES_IDS = {"XAUUSD": "GC=F", "US100": "NQ=F", "US30": "YM=F"}
for _n in INSTRUMENTS:  # FALLBACK_XAUUSD=GC=F,binance:PAXGUSDT overrides the stand-in order ("none" = no fallback)
    _fb = os.environ.get(f"FALLBACK_{_n}")
    if _fb:
        INSTRUMENTS[_n]["fallbacks"] = [] if _fb.lower() == "none" else [x.strip() for x in _fb.split(",")]
_fut = os.environ.get("FUTURES", "").strip().upper()
for _n, _id in FUTURES_IDS.items():  # FUTURES=1 for all, or a list such as FUTURES=US100,US30
    if _fut == "1" or _n in [x.strip() for x in _fut.split(",")]:
        INSTRUMENTS[_n]["id"] = _id


# ===== Data =====

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def parse_binance(rows):
    return [{
        "time": datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc),
        "open": float(r[1]), "high": float(r[2]),
        "low": float(r[3]), "close": float(r[4]),
    } for r in rows]


def parse_yahoo(data):
    result = data["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(result.get("timestamp") or []):
        o, h, l, c = quote["open"][i], quote["high"][i], quote["low"][i], quote["close"][i]
        if None in (o, h, l, c):
            continue
        out.append({"time": datetime.fromtimestamp(t, tz=timezone.utc),
                    "open": o, "high": h, "low": l, "close": c})
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
        else:
            out.append({"start": start, "open": c["open"], "high": c["high"],
                        "low": c["low"], "close": c["close"]})
    return [{"time": datetime.fromtimestamp(o["start"], tz=timezone.utc), "open": o["open"],
             "high": o["high"], "low": o["low"], "close": o["close"]} for o in out]


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


def selective_scan(name, now_ts, scfg, store, offsets=None, spreads=None, specs=None, lang="en",
                   news=None, news_block=False, balance=0.0, risk_percent=0.5, contracts=None):
    """Runs the selective engine for one market.
    Returns (status_text, alert_or_None, lagging). lagging=True means the M5 candle that just
    closed is not listed by the feed yet, so the caller should try again in a minute."""
    market = markets.canonical(name)
    if market not in markets.DEFAULTS:
        return f"{name}: the selective engine covers XAUUSD, NAS100, US30 and BTCUSD only", None, False
    inst = INSTRUMENTS[name]
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
        return f"{name}: SELECTIVE WAIT (data {status})", None, False
    m5_closed = selective.closed_only(data["m5"], 300, now_ts)
    if m5_closed and int(m5_closed[-1]["time"].timestamp()) + 300 < now_ts - now_ts % 300:
        return f"{name}: waiting for the latest M5 candle to be listed", None, True
    for tf, key in SELECTIVE_TFS:  # same data-quality gate as the other engines
        quality = data_layer.check_candles(data[key], tf, now_ts, inst["source"] == "binance", min_bars=1)
        if quality.blocked:
            return f"{name}: SELECTIVE WAIT (data quality {tf}: {quality.reason})", None, False
    if news_block and extras.near_news(news or [], name, now_ts):
        near = extras.near_news(news, name, now_ts)[0]
        return f"{name}: SELECTIVE WAIT (high-impact news: {near['currency']} {near['title']})", None, False
    spec = (specs or {}).get(market)
    spread = (spreads or {}).get(name)
    if spread is None and spec is not None:
        spread = spec.spread
    commission_price = None
    if spec is not None and spec.commission_per_lot is not None and spec.tick_size and spec.tick_value:
        commission_price = spec.commission_per_lot / (spec.tick_value / spec.tick_size)
    res = selective.process(market, data, now_ts, store, scfg, spread, commission_price, status,
                            calendar=structure.calendar_for(inst["source"]))
    d = inst["digits"]
    if res["action"] == "WAIT":
        line = f"{name}: SELECTIVE WAIT ({res['reasons'][0] if res['reasons'] else 'no setup'})"
        stage = res["report"].get("reversal", "")
        if stage.startswith("stage") and not stage.startswith(("stage 0", "stage 1")):
            line += f" [setup forming: reversal {stage.split(':')[0].split(' complete')[0]}]"
        if os.environ.get("VERBOSE") == "1":  # full report: HTF, liquidity, structure, zones, retest, M5 candle
            line += "\n" + selective.explain(res)
        return line, None, False
    setup = {k: (round(v, d) if isinstance(v, float) else v) for k, v in res["setup"].items()}
    fmt = selective.format_signal_ar if lang == "ar" else selective.format_signal
    text = fmt(name, dict(res, setup=setup), d)
    warn = fallback_note(inst)
    if warn:
        text += "\n" + warn
    lot = extras.lot_text(balance, risk_percent, setup, spec, (contracts or {}).get(name)).strip()
    if lot:
        text += "\n" + lot
    return f"{name}: SELECTIVE {res['action']}", {"text": text, "setup": setup, "market": market,
                                                  "bar_time": setup["bar_time"]}, False


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


def notify(text, entry=False, side=None):
    """Shows the message. Only entry=True (a trade ready to enter) makes a sound; everything
    else (sessions, news, results, heads-up events) is only printed on the Termux screen
    (INFO_NOTIFY=1 also shows it as a silent phone notification).
    side 'buy' / 'sell' colours the alert: blue for BUY, red for SELL (terminal colour, a coloured
    circle in the notification and Telegram text, and the phone's notification light)."""
    emoji, ansi, led = SIDE_STYLE.get(side, ("", "", ""))
    print(f"{ansi}{text}\033[0m" if ansi else text, flush=True)
    if not entry and os.environ.get("INFO_NOTIFY") != "1":
        return  # info messages stay in the Termux screen only: no phone notification, no sound
    if emoji:
        text = f"{emoji} {text}"
    sound = sound_path() if entry and shutil.which("termux-media-player") else None
    if shutil.which("termux-notification"):
        title = "ICT SMC" if entry else "ICT SMC (info)"
        if side:
            title = f"{emoji} {side.upper()} - ICT SMC"
        cmd = ["termux-notification", "--title", title,
               "--content", text, "--priority", "high" if entry else "low"]
        if led:
            cmd += ["--led-color", led]
        if entry and not sound:
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
            if not entry:
                fields["disable_notification"] = "true"  # no sound on the Telegram side either
            body = urllib.parse.urlencode(fields).encode()
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", data=body, timeout=20)
        except Exception:
            log.exception("Telegram send failed")


# ===== Main loop =====

def run():
    names = [markets.feed_name(n) for n in os.environ.get(
        "SYMBOLS", "BTCUSD,XAUUSD,US100,US30,EURUSD").split(",") if n.strip()]
    unknown = [n for n in names if n not in INSTRUMENTS]
    if unknown:
        raise SystemExit(f"Unknown symbols: {unknown}. Choose from {list(INSTRUMENTS)}")

    offsets = {}
    for n in names:
        raw = os.environ.get(f"OFFSET_{n}")
        if raw:
            try:
                offsets[n] = float(raw)
            except ValueError:
                raise SystemExit(f"OFFSET_{n} must be a number, got {raw!r}")

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
                 risk_percent=number("RISK_PERCENT", 0.5))
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
    alert_lang = os.environ.get("ALERT_LANG", "en").lower()  # en (default) or ar
    opts["news_block"] = os.environ.get("NEWS_BLOCK", "1" if use_selective else "0") == "1"
    opts["legacy_ict"] = engine in ("legacy", "both")
    if engine == "selective":  # the old classic / RCT alerts are only for ENGINE=legacy or both
        cfg.classic_enabled = False
        cfg.rct_enabled = False
    profile = os.environ.get("PROFILE", "strict").lower()
    if profile not in ("strict", "active"):
        raise SystemExit("PROFILE must be strict or active")
    base_conf = 4 if profile == "active" else 5
    scfg = selective.make_config(
        profile, min_confluence=int(number("MIN_CONFLUENCE", base_conf)), slippage=number("SLIPPAGE", 0.0),
        max_daily_losses=int(number("MAX_DAILY_LOSSES", 2)),
        max_daily_loss_r=number("MAX_DAILY_LOSS_R", 3.0),
        allow_unknown_cost=os.environ.get("ALLOW_UNKNOWN_COST") == "1")
    spreads = {n: number(f"SPREAD_{n}", None) for n in names if os.environ.get(f"SPREAD_{n}")}
    store = signal_store.SignalStore(os.environ.get("STATE_FILE", signal_store.DEFAULT_FILE)) \
        if use_selective else None
    track = tracker.Tracker(os.environ.get("RESULTS_FILE", tracker.DEFAULT_CSV)) \
        if os.environ.get("TRACK", "1") != "0" else None
    last_bucket = {}
    if store and track:
        track.on_done = lambda it, outcome: store.close(it["ref"], outcome) if it.get("ref") else None
        for market, sig in list(store.active_signals.items()):  # keep following signals after a restart
            feed = markets.feed_name(market)
            if feed in names:
                track.add(feed, "5m", "selective", sig, sig["bar_time"], ref=market, market=True)
    if store and store.load_error:
        print(f"WARNING: signal state file was unreadable ({store.load_error}); started clean", flush=True)
    us_alerts = os.environ.get("US_OPEN_ALERTS", "1") != "0"
    session_alerts = os.environ.get("SESSION_ALERTS", "1") != "0"
    last_sessions = None
    use_news = os.environ.get("NEWS", "1") != "0"
    news_fetched = 0
    seen = set()
    print(f"ICT SMC alerts started for: {', '.join(names)} on {', '.join(tfs)}", flush=True)
    if offsets:
        print(f"Price offsets: {offsets}", flush=True)

    last_beat = 0
    while True:
        now_ts = int(time.time())
        if now_ts - last_beat >= 3600:
            last_beat = now_ts
            print(f"[{datetime.fromtimestamp(now_ts, tz=timezone.utc):%H:%M UTC}] bot is running", flush=True)
        if use_news and now_ts - news_fetched > 3600:
            news_fetched = now_ts  # also on failure, so we retry next hour
            try:
                opts["news"] = extras.fetch_news()
            except Exception as e:
                print(f"news calendar unavailable: {e}", flush=True)
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
        if use_selective and now_ts % 300 < 180:  # first 3 minutes of each M5 candle
            bucket = now_ts // 300
            for name in names:
                if last_bucket.get(("sel", name)) == bucket:
                    continue
                try:
                    status, alert, lagging = selective_scan(
                        name, now_ts, scfg, store, offsets, spreads, opts["markets"], alert_lang,
                        opts["news"], opts["news_block"], opts["balance"], cfg.risk_percent,
                        opts["contracts"])
                    if not lagging:
                        last_bucket[("sel", name)] = bucket
                    print(f"[{stamp} sel] {status}", flush=True)
                    if alert:
                        notify(alert["text"], entry=True, side=alert["setup"]["side"])
                        if track:
                            track.add(name, "5m", "selective", alert["setup"], alert["bar_time"],
                                      ref=alert["market"], market=True)
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
                            notify(note)
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
