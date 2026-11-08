"""ICT SMC alerts: same signal rules as the EA, but it only sends alerts (no orders).

Runs on Termux with the Python standard library only (no pip install needed).
Prices come from free public sources, so they can differ from your broker's prices.

Environment variables (all optional):
    SYMBOLS           comma list, default BTCUSD,XAUUSD,US100,US30
    OFFSET_<SYMBOL>   e.g. OFFSET_XAUUSD=-12.5, added to feed prices (broker price - feed price)
    MIN_SCORE         0-4: ICT alerts need at least this many classical confirmations (default 0)
    CLASSIC           0 turns off the standalone classical alerts (default 1)
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

from classic import find_classic_setup, ict_confluence
from ict_smc_bot import Config, find_setup, to_series

log = logging.getLogger("ict_alerts")

BAR_SECONDS = 300
STALE_SECONDS = 600  # newest candle older than this = market closed or data delayed

# name -> free data source. Gold and indices use Yahoo futures (GC=F, NQ=F, YM=F),
# bitcoin uses Binance spot. Futures prices can sit a little away from broker CFD prices.
INSTRUMENTS = {
    "BTCUSD": {"source": "binance", "id": "BTCUSDT", "digits": 2},
    "XAUUSD": {"source": "yahoo", "id": "GC=F", "digits": 2},
    "US100": {"source": "yahoo", "id": "NQ=F", "digits": 1},
    "US30": {"source": "yahoo", "id": "YM=F", "digits": 1},
}


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


def get_candles(inst):
    if inst["source"] == "binance":
        last_error = None
        for host in ("https://api.binance.com", "https://data-api.binance.vision"):
            try:
                rows = fetch_json(f"{host}/api/v3/klines?symbol={inst['id']}&interval=5m&limit=300")
                return parse_binance(rows)
            except Exception as e:  # try the next host
                last_error = e
        raise last_error
    sym = urllib.parse.quote(inst["id"], safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=5m&range=5d"
    return parse_yahoo(fetch_json(url))[-300:]


# ===== Analysis =====

def format_alert(kind, name, setup, price, d, cfg, r, points, offset):
    risk = abs(setup["entry"] - setup["sl"])
    side = setup["side"].upper()
    if kind == "ict":
        head = f"ICT SMC ALERT: {side} LIMIT {name}"
        extra = f"Confluence: {len(points)}/4" + (f" ({', '.join(points)})" if points else "") + "\n"
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


def analyze(name, cfg, now_ts, seen, offsets=None):
    """Returns (status_text, alerts); alerts is a list of {'kind', 'setup', 'text'}.

    offsets: optional {name: number} added to every feed price so the levels match
    your broker (offset = broker price - feed price).
    """
    inst = INSTRUMENTS[name]
    candles = get_candles(inst)
    if not candles:
        return f"{name}: no data", []

    offset = (offsets or {}).get(name, 0.0)
    if offset:
        candles = [dict(c, open=c["open"] + offset, high=c["high"] + offset,
                        low=c["low"] + offset, close=c["close"] + offset)
                   for c in candles]

    r = to_series(candles)
    forming = now_ts - now_ts % BAR_SECONDS
    newest = int(r[0]["time"].timestamp())

    if newest < forming - STALE_SECONDS:
        age_min = (now_ts - newest) // 60
        return f"{name}: market closed or data delayed (last candle {age_min} min old)", []

    # The signal rules need r[0] to be the bar that is still forming.
    if newest < forming:
        placeholder = dict(r[0])
        placeholder["time"] = datetime.fromtimestamp(forming, tz=timezone.utc)
        r.insert(0, placeholder)

    d = inst["digits"]
    price = r[0]["close"]
    status = f"{name}: price {price:.{d}f} ok"
    if offset:
        status += f" (feed {price - offset:.{d}f}, offset {offset:+g})"

    bar_time = int(r[1]["time"].timestamp())
    alerts = []

    setup = find_setup(r, cfg, ask=price, bid=price, digits=d)
    if setup:
        points = ict_confluence(r, cfg, setup)
        key = (name, "ict", bar_time)
        if len(points) >= cfg.min_score and key not in seen:
            seen.add(key)
            alerts.append({"kind": "ict", "setup": setup,
                           "text": format_alert("ict", name, setup, price, d, cfg, r, points, offset)})

    if cfg.classic_enabled:
        classic = find_classic_setup(r, cfg)
        key = (name, "classic", bar_time)
        if classic and key not in seen:
            seen.add(key)
            classic = {k: (round(v, d) if k != "side" else v) for k, v in classic.items()}
            alerts.append({"kind": "classic", "setup": classic,
                           "text": format_alert("classic", name, classic, price, d, cfg, r, [], offset)})

    return status, alerts


# ===== Notifications =====

def notify(text):
    print(text, flush=True)
    if shutil.which("termux-notification"):
        try:
            subprocess.run(["termux-notification", "--title", "ICT SMC",
                            "--content", text, "--priority", "high", "--sound"],
                           timeout=10)
        except Exception:
            log.exception("termux-notification failed")
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
    names = [n.strip().upper() for n in os.environ.get(
        "SYMBOLS", "BTCUSD,XAUUSD,US100,US30").split(",") if n.strip()]
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

    cfg = Config(min_score=int(os.environ.get("MIN_SCORE", "0")),
                 classic_enabled=os.environ.get("CLASSIC", "1") != "0")
    seen = set()
    print(f"ICT SMC alerts started for: {', '.join(names)}", flush=True)
    if offsets:
        print(f"Price offsets: {offsets}", flush=True)

    while True:
        now_ts = int(time.time())
        stamp = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%H:%M UTC")
        for name in names:
            try:
                status, alerts = analyze(name, cfg, now_ts, seen, offsets)
                print(f"[{stamp}] {status}", flush=True)
                for alert in alerts:
                    notify(alert["text"])
            except Exception as e:
                print(f"[{stamp}] {name}: error: {e}", flush=True)
        # wake up shortly after the next 5-minute candle opens
        time.sleep(max(1, BAR_SECONDS - time.time() % BAR_SECONDS + 8))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
