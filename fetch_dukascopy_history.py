"""Download SPOT-based historical prices from Dukascopy (free, no key) and build M5 candles as a CSV
for backtest_runner.py. For gold this is far closer to a broker's XAUUSD than Binance PAXG or the
GC=F futures, because Dukascopy quotes spot.

Run it where the internet works (your phone in Termux). Standard library only (needs Python's lzma).
Resumable: run it again and it continues after the last saved candle.

    python fetch_dukascopy_history.py --symbol XAUUSD --days 90 --out XAUUSD_M5.csv

How it works (format taken from public Dukascopy-reading scripts, NOT from official documentation,
so check the first run): one file per hour at
  https://datafeed.dukascopy.com/datafeed/<SYMBOL>/<YYYY>/<MM zero-based>/<DD>/<HH>h_ticks.bi5
LZMA-compressed, 20 bytes per tick, big endian: ms since the hour, ask, bid (integers, price x scale),
ask volume, bid volume (float32). Candles are built from the BID, like a broker chart. Times are UTC.
A 404 (weekend, holiday) is normal and skipped. Any other failure (503, time-out, 403) is retried with waits and, if it persists, the run STOPS at that hour so the file never has hidden gaps; run it again later to continue.

Price scale: XAUUSD uses 1000. Other symbols differ and are NOT known here: pass --scale yourself and
compare the printed sample price with the real price before trusting a file. Symbol codes for other
markets (indices, BTC) are also unverified: check them on Dukascopy before using them.
"""
import argparse
import csv
import lzma
import os
import struct
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

URL = "https://datafeed.dukascopy.com/datafeed/{s}/{y:04d}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"
SCALES = {"XAUUSD": 1000.0}
TICK = struct.Struct(">IIIff")          # ms, ask, bid, ask volume, bid volume
M5 = 300


def fetch_hour(symbol, when, opener=urllib.request.urlopen):
    """Raw bytes of one hour file, or b'' when Dukascopy has none (404 / empty)."""
    url = URL.format(s=symbol, y=when.year, m=when.month - 1, d=when.day, h=when.hour)   # month is zero-based
    try:
        with opener(urllib.request.Request(url, headers={"User-Agent": "ict-smc-bot-backtest"}), timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:                                          # no data for that hour: normal
            return b""
        raise


class DownloadStopped(Exception):
    """A required hour could not be downloaded even after retries. Nothing after it was saved, so the
    file has no gap: run the command again later and it continues from this hour."""


def fetch_with_retry(fetch, symbol, when, tries=7, sleep=time.sleep, log=print):
    """fetch_hour with retries and growing waits for 429/5xx errors, time-outs and dropped connections.
    A 404 (no data for that hour) is not an error: fetch_hour returns b'' for it."""
    last = None
    for n in range(tries):
        try:
            return fetch(symbol, when)
        except (OSError, ValueError) as e:                       # URLError, HTTPError, TimeoutError, ...
            last = e
            if n < tries - 1:
                wait = min(3 * 2 ** n, 60)                         # 3, 6, 12, 24, 48, 60 seconds
                log(f"  {when:%Y-%m-%d %H}h: {e}; retry {n + 1}/{tries - 1} in {wait}s")
                sleep(wait)
    raise DownloadStopped(f"{when:%Y-%m-%d %H}h failed after {tries} tries: {last}")


def parse_ticks(blob, hour_start, scale):
    """[(epoch_seconds_float, bid)] from one hour file. An unreadable file raises ValueError."""
    if not blob:
        return []
    try:
        raw = lzma.decompress(blob, format=lzma.FORMAT_ALONE)
    except lzma.LZMAError:
        try:
            raw = lzma.decompress(blob)
        except lzma.LZMAError as e:
            raise ValueError(f"cannot decompress: {e}") from None
    base = int(hour_start.timestamp())
    return [(base + ms / 1000.0, bid / scale)
            for ms, _ask, bid, _av, _bv in (TICK.unpack_from(raw, i) for i in range(0, len(raw) - 19, 20))]


def to_m5(ticks):
    """Aggregate (time, bid) ticks into {bucket_start: [o, h, l, c]} for 5-minute buckets."""
    out = {}
    for t, p in ticks:
        k = int(t) - int(t) % M5
        c = out.get(k)
        if c is None:
            out[k] = [p, p, p, p]
        else:
            c[1], c[2], c[3] = max(c[1], p), min(c[2], p), p
    return out


def last_saved(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        rows = list(csv.reader(f))
    return int(float(rows[-1][0])) if len(rows) > 1 else None


def download(symbol, days, out, scale=None, fetch=fetch_hour, now=None, sleep=time.sleep, log=print,
             skip_failed=False, delay=1.0):
    scale = scale or SCALES.get(symbol)
    if not scale:
        raise SystemExit(f"price scale for {symbol} is unknown: pass --scale (XAUUSD is 1000)")
    now = now or datetime.now(timezone.utc)
    resume = last_saved(out)
    start = (datetime.fromtimestamp(resume + M5, timezone.utc) if resume
             else now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    new_file = resume is None
    saved, hours, sample = 0, 0, []
    cur = start
    with open(out, "w" if new_file else "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time", "open", "high", "low", "close"])
        while cur + timedelta(hours=1) <= now:                    # only hours that are over
            try:
                blob = fetch_with_retry(fetch, symbol, cur, tries=tries, sleep=sleep, log=log)
                ticks = parse_ticks(blob, cur, scale)
            except (DownloadStopped, ValueError) as e:
                if not skip_failed:
                    f.flush()
                    raise e if isinstance(e, DownloadStopped) else DownloadStopped(f"{cur:%Y-%m-%d %H}h: {e}")
                log(f"  {cur:%Y-%m-%d %H}h SKIPPED (gap in the data): {e}")
                ticks = []
            for k, (o, h, l, c) in sorted(to_m5(ticks).items()):
                if resume and k <= resume:
                    continue
                w.writerow([k, f"{o:.5f}", f"{h:.5f}", f"{l:.5f}", f"{c:.5f}"])
                saved += 1
                if len(sample) < 3:
                    sample.append(c)
            hours += 1
            if hours % 24 == 0:
                f.flush()
                log(f"  {cur:%Y-%m-%d}: {saved} candles saved")
            cur += timedelta(hours=1)
            sleep(delay)                                           # be gentle: the server throttles
    if sample:
        log(f"first prices saved: {', '.join(f'{p:.2f}' for p in sample)}  "
            "<- compare with the real price of that time; a wrong --scale makes them wildly off")
    return saved


def main(argv=None):
    ap = argparse.ArgumentParser(description="Download Dukascopy spot prices as M5 candles.")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--scale", type=float, help="integer price divisor (XAUUSD = 1000)")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests (default 1; raise it if the server keeps refusing)")
    ap.add_argument("--pause", type=float, default=0.3, help="seconds between requests (slower = fewer 503s)")
    ap.add_argument("--tries", type=int, default=5, help="attempts per hour before stopping")
    ap.add_argument("--skip-failed", action="store_true",
                    help="skip hours that keep failing (leaves GAPS in the data; off by default)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        n = download(a.symbol, a.days, a.out, a.scale, skip_failed=a.skip_failed, delay=a.delay)
    except DownloadStopped as e:
        raise SystemExit(f"STOPPED: {e}\nThe file is complete up to that hour. Wait a few minutes and run the "
                         "same command again: it continues from there.")
    print(f"done: {n} new candles in {a.out}")


if __name__ == "__main__":
    main()
