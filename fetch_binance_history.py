"""Download historical candles from Binance (free, no key) into a CSV for backtest_runner.py.

Run it where the internet works (your phone in Termux). Only the standard library is needed.
It saves what it has after every page, so you can stop it and run it again: it resumes.

    python fetch_binance_history.py --symbol BTCUSDT --days 365 --out BTCUSDT_M5.csv

Binance BTCUSDT is NOT your broker's BTCUSD: prices and spreads differ a little. Good enough to
measure the engine on crypto, not to prove results on your broker's feed. Times are UTC.
"""
import argparse
import csv
import json
import os
import time
import urllib.request

URL = "https://api.binance.com/api/v3/klines?symbol={s}&interval={i}&startTime={t}&limit=1000"
STEP_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "1d": 86_400_000}


def fetch_page(symbol, interval, start_ms, opener=urllib.request.urlopen):
    with opener(URL.format(s=symbol, i=interval, t=start_ms), timeout=20) as r:
        return json.load(r)


def last_time_ms(path):
    """Open time (ms) of the last saved candle, or None for a new file."""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return None
    return int(float(rows[-1][0]) * 1000)


def download(symbol, interval, days, out, fetch=fetch_page, now_ms=None, sleep=time.sleep, log=print):
    step = STEP_MS[interval]
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    resume = last_time_ms(out)
    start = (resume + step) if resume else int(now_ms - days * 86_400_000)
    new_file = resume is None
    total = 0
    with open(out, "w" if new_file else "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time", "open", "high", "low", "close"])      # time = epoch seconds, UTC
        while start < now_ms:
            page = fetch(symbol, interval, start)
            if not page:
                break
            for k in page:
                if k[0] + step > now_ms:                              # a candle that is still forming
                    continue
                w.writerow([k[0] // 1000, k[1], k[2], k[3], k[4]])
                total += 1
            f.flush()
            start = page[-1][0] + step
            log(f"  {total} candles saved")
            sleep(0.25)
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description="Download Binance candles to CSV.")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="5m", choices=list(STEP_MS))
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    n = download(a.symbol, a.interval, a.days, a.out)
    print(f"done: {n} new candles in {a.out}")


if __name__ == "__main__":
    main()
