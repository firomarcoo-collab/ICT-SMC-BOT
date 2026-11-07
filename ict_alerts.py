"""ICT SMC alerts: same signal rules as the EA, but it only sends alerts (no orders).

Runs on Termux with the Python standard library only (no pip install needed).
Prices come from free public sources, so they can differ from your broker's prices.

Environment variables (all optional):
    SYMBOLS           comma list, default BTCUSD,XAUUSD,US100,US30
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
                rows = fetch_json(f"{host}/api/v3/klines?symbol={inst['id']}&interval=5m&limit=60")
                return parse_binance(rows)
            except Exception as e:  # try the next host
                last_error = e
        raise last_error
    sym = urllib.parse.quote(inst["id"], safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=5m&range=5d"
    return parse_yahoo(fetch_json(url))[-60:]


# ===== Analysis =====

def analyze(name, cfg, now_ts, seen):
    """Returns (status_text, setup_or_None, alert_text_or_None)."""
    inst = INSTRUMENTS[name]
    candles = get_candles(inst)
    if not candles:
        return f"{name}: no data", None, None

    r = to_series(candles)
    forming = now_ts - now_ts % BAR_SECONDS
    newest = int(r[0]["time"].timestamp())

    if newest < forming - STALE_SECONDS:
        age_min = (now_ts - newest) // 60
        return f"{name}: market closed or data delayed (last candle {age_min} min old)", None, None

    # The signal rules need r[0] to be the bar that is still forming.
    if newest < forming:
        placeholder = dict(r[0])
        placeholder["time"] = datetime.fromtimestamp(forming, tz=timezone.utc)
        r.insert(0, placeholder)

    price = r[0]["close"]
    status = f"{name}: price {price:.{inst['digits']}f} ok"

    setup = find_setup(r, cfg, ask=price, bid=price, digits=inst["digits"])
    if not setup:
        return status, None, None

    key = (name, int(r[1]["time"].timestamp()))
    if key in seen:
        return status, setup, None
    seen.add(key)

    d = inst["digits"]
    risk = abs(setup["entry"] - setup["sl"])
    alert = (
        f"ICT SMC ALERT: {setup['side'].upper()} LIMIT {name}\n"
        f"Entry: {setup['entry']:.{d}f}\n"
        f"SL:    {setup['sl']:.{d}f}\n"
        f"TP:    {setup['tp']:.{d}f}\n"
        f"Risk distance: {risk:.{d}f} | RR {cfg.rr}\n"
        f"Feed price now: {price:.{d}f} (compare with MT5, shift the levels if it differs)\n"
        f"Candle: {r[1]['time'].strftime('%Y-%m-%d %H:%M')} UTC"
    )
    return status, setup, alert


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

    cfg = Config()
    seen = set()
    print(f"ICT SMC alerts started for: {', '.join(names)}", flush=True)

    while True:
        now_ts = int(time.time())
        stamp = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%H:%M UTC")
        for name in names:
            try:
                status, _setup, alert = analyze(name, cfg, now_ts, seen)
                print(f"[{stamp}] {status}", flush=True)
                if alert:
                    notify(alert)
            except Exception as e:
                print(f"[{stamp}] {name}: error: {e}", flush=True)
        # wake up shortly after the next 5-minute candle opens
        time.sleep(max(1, BAR_SECONDS - time.time() % BAR_SECONDS + 8))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
