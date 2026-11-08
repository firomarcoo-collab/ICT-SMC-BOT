"""ICT SMC alerts: same signal rules as the EA, but it only sends alerts (no orders).

Runs on Termux with the Python standard library only (no pip install needed).
Prices come from free public sources, so they can differ from your broker's prices.

Environment variables (all optional):
    SYMBOLS           comma list, default BTCUSD,XAUUSD,US100,US30
    OFFSET_<SYMBOL>   e.g. OFFSET_XAUUSD=-12.5, added to feed prices (broker price - feed price)
    MIN_SCORE         0-4: ICT alerts need at least this many classical confirmations (default 0)
    FUTURES           1 = use futures for gold/indices instead of spot (default: spot)
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
import tracker
from classic import find_classic_setup, ict_confluence, trend
from ict_smc_bot import Config, find_setup, to_series

log = logging.getLogger("ict_alerts")

TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
PROFILE = {"1m": "scalp", "5m": "scalp", "15m": "scalp", "1h": "swing", "4h": "swing", "1d": "swing"}
BAR_SECONDS = 300  # default timeframe (5m)
# Yahoo has no 4h candles: they are built from 1h candles. (interval, range) per timeframe.
YAHOO_TF = {"1m": ("1m", "5d"), "5m": ("5m", "5d"), "15m": ("15m", "1mo"),
            "1h": ("60m", "3mo"), "4h": ("60m", "1y"), "1d": ("1d", "2y")}
LAST_R = {}  # (symbol, timeframe) -> candles of the last analysis, newest first
TREND_CACHE = {}  # (symbol, timeframe) -> 'up' / 'down' / 'flat', filled while analysing


def stale_after(bar):
    """Seconds after a bar's start when we call the market closed / data delayed."""
    if bar <= 900:
        return bar + 600
    return bar * 2 if bar < 86400 else bar + 3 * 86400

# name -> free data source. Default is SPOT prices: bitcoin from Binance, gold from
# Yahoo spot XAUUSD=X, indices from the Yahoo cash indices ^NDX and ^DJI.
# FUTURES=1 switches gold/indices to futures (GC=F, NQ=F, YM=F).
INSTRUMENTS = {
    "BTCUSD": {"source": "binance", "id": "BTCUSDT", "digits": 2},
    "XAUUSD": {"source": "yahoo", "id": "XAUUSD=X", "digits": 2},
    "US100": {"source": "yahoo", "id": "^NDX", "digits": 1},
    "US30": {"source": "yahoo", "id": "^DJI", "digits": 1},
    "EURUSD": {"source": "yahoo", "id": "EURUSD=X", "digits": 5},
}
FUTURES_IDS = {"XAUUSD": "GC=F", "US100": "NQ=F", "US30": "YM=F"}
if os.environ.get("FUTURES") == "1":
    for _n, _id in FUTURES_IDS.items():
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


def get_candles(inst, tf="5m"):
    if inst["source"] == "binance":
        last_error = None
        for host in ("https://api.binance.com", "https://data-api.binance.vision"):
            try:
                rows = fetch_json(f"{host}/api/v3/klines?symbol={inst['id']}&interval={tf}&limit=300")
                return parse_binance(rows)
            except Exception as e:  # try the next host
                last_error = e
        raise last_error
    interval, span = YAHOO_TF[tf]
    sym = urllib.parse.quote(inst["id"], safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval={interval}&range={span}"
    candles = parse_yahoo(fetch_json(url))
    if tf == "4h":
        candles = resample(candles, TF_SECONDS["4h"])
    return candles[-300:]


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

    setup = find_setup(r, cfg, ask=price, bid=price, digits=d)
    if setup:
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


def notify(text):
    print(text, flush=True)
    sound = sound_path() if shutil.which("termux-media-player") else None
    if shutil.which("termux-notification"):
        cmd = ["termux-notification", "--title", "ICT SMC",
               "--content", text, "--priority", "high"]
        if not sound:
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
            body = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
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
    tfs = [t.strip().lower() for t in os.environ.get(
        "TIMEFRAMES", "1m,5m,15m,1h,4h,1d").split(",") if t.strip()]
    bad = [t for t in tfs if t not in TF_SECONDS]
    if bad:
        raise SystemExit(f"Unknown timeframes: {bad}. Choose from {list(TF_SECONDS)}")
    event_tfs = {t.strip().lower() for t in os.environ.get(
        "EVENT_TIMEFRAMES", "15m,1h,4h,1d").split(",") if t.strip()}
    cfg.rct_enabled = os.environ.get("RCT", "1") != "0"
    track = tracker.Tracker(os.environ.get("RESULTS_FILE", tracker.DEFAULT_CSV)) \
        if os.environ.get("TRACK", "1") != "0" else None
    last_bucket = {}
    us_alerts = os.environ.get("US_OPEN_ALERTS", "1") != "0"
    session_alerts = os.environ.get("SESSION_ALERTS", "1") != "0"
    last_sessions = None
    use_news = os.environ.get("NEWS", "1") != "0"
    news_fetched = 0
    seen = set()
    print(f"ICT SMC alerts started for: {', '.join(names)} on {', '.join(tfs)}", flush=True)
    if offsets:
        print(f"Price offsets: {offsets}", flush=True)

    while True:
        now_ts = int(time.time())
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
                        notify(alert["text"])
                        if track and alert["setup"]:
                            track.add(name, tf, alert["kind"], alert["setup"], alert["bar_time"])
                    if track and (name, tf) in LAST_R:
                        for note in track.update(name, tf, LAST_R[(name, tf)]):
                            notify(note)
                except Exception as e:
                    print(f"[{stamp} {tf}] {name}: error: {e}", flush=True)
        # wake up shortly after the next minute starts
        time.sleep(max(1, 60 - time.time() % 60 + 8))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
