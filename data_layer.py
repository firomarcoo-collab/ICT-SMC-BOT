"""Data layer (phase 1): providers, data-quality checks, and a fingerprint of the data
behind every signal.

Providers
- FreeFeedProvider: Binance / Yahoo through the existing ict_alerts feeds (works on Android).
- MT5Provider: NOT implemented. The MetaTrader5 Python package runs on Windows only and this
  project is used from an Android phone, so it cannot be written or tested here. It raises
  an explicit error instead of pretending.

Quality checks (check_candles) - what blocks analysis and what only warns:
  BLOCKS : too few bars, unsorted/duplicate times, impossible OHLC (high < low, non-positive
           price), candle times in the future (typical sign of a server-time offset), newest
           candle too old (market closed or feed delayed), an extreme outlier in the last
           bars (range above BLOCK_RANGE_X times the typical range: almost surely a bad tick).
  WARNS  : missing candles inside trading hours, a strong move in the last bars
           (WARN_RANGE_X times the typical range: may be real news).
  INFO   : market breaks (weekend, daily close): gaps longer than MAX_MISSING_BARS bars on
           non-24/7 markets are expected and are not reported as missing.
"""
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

MIN_BARS = 250
WARN_RANGE_X = 8.0
BLOCK_RANGE_X = 25.0
MAX_MISSING_BARS = 12       # a gap of up to this many bars is "missing candles"; more = a break
MARKET_BREAK_SECONDS = 3600  # on non-24/7 markets, gaps of this length or more are market breaks
RECENT_BARS = 3             # the last closed bars are the ones that decide a signal

TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def stale_after(bar):
    """Seconds after a bar's start when the market is called closed / the feed delayed."""
    if bar <= 900:
        return bar + 600
    return bar * 2 if bar < 86400 else bar + 3 * 86400


@dataclass
class Report:
    n: int = 0
    hard: list = field(default_factory=list)       # reasons that block analysis
    warnings: list = field(default_factory=list)
    info: list = field(default_factory=list)
    gaps: list = field(default_factory=list)       # [{'after', 'missing'}] inside trading hours
    outliers: list = field(default_factory=list)   # [{'time', 'x'}] range / typical range
    age_seconds: int = 0

    @property
    def blocked(self):
        return bool(self.hard)

    @property
    def reason(self):
        return "; ".join(self.hard)

    def summary(self):
        if self.hard:
            return "DATA BLOCKED: " + self.reason
        if self.warnings:
            return "data ok with warnings: " + "; ".join(self.warnings)
        return "data ok"


def _ts(c):
    return int(c["time"].timestamp())


def _typical_range(candles, end):
    """Median high-low of the (up to) 50 bars before index `end`."""
    sample = sorted(c["high"] - c["low"] for c in candles[max(0, end - 50):end]
                    if c["high"] - c["low"] > 0)
    return sample[len(sample) // 2] if sample else 0.0


def check_candles(candles, tf, now_ts=None, continuous=False, min_bars=MIN_BARS):
    """Validate candles (any order). continuous=True for 24/7 markets such as crypto."""
    now_ts = int(time.time()) if now_ts is None else now_ts
    bar = TF_SECONDS[tf]
    rep = Report(n=len(candles))
    if not candles:
        rep.hard.append("no data")
        return rep
    if len(candles) < min_bars:
        rep.hard.append(f"only {len(candles)} bars, {min_bars} needed")

    times = [_ts(c) for c in candles]
    if times != sorted(times):
        rep.hard.append("candles are not in time order")
    if len(set(times)) != len(times):
        rep.hard.append("duplicate candle times")
    ordered = sorted(candles, key=_ts)

    bad = [c for c in ordered if min(c["open"], c["high"], c["low"], c["close"]) <= 0
           or c["high"] < max(c["open"], c["close"], c["low"])
           or c["low"] > min(c["open"], c["close"], c["high"])]
    if bad:
        rep.hard.append(f"{len(bad)} candles with impossible OHLC (first at "
                        f"{bad[0]['time'].strftime('%Y-%m-%d %H:%M')} UTC)")

    newest = _ts(ordered[-1])
    rep.age_seconds = now_ts - newest
    if newest > now_ts + bar:
        rep.hard.append(f"newest candle is {newest - now_ts} s in the future "
                        f"(server time offset or bad feed)")
    elif rep.age_seconds > stale_after(bar):
        rep.hard.append(f"market closed or data delayed (last candle {rep.age_seconds // 60} min old)")

    # gaps
    for a, b in zip(ordered, ordered[1:]):
        delta = _ts(b) - _ts(a)
        if delta <= bar:
            continue
        missing = delta // bar - 1
        is_break = (not continuous) and (missing > MAX_MISSING_BARS or delta >= MARKET_BREAK_SECONDS)
        if is_break:
            rep.info.append(f"market break after {a['time'].strftime('%m-%d %H:%M')}")
        else:
            rep.gaps.append({"after": a["time"], "missing": missing})
    if rep.gaps:
        total = sum(g["missing"] for g in rep.gaps)
        rep.warnings.append(f"{total} missing candles in {len(rep.gaps)} gaps")
        if (ordered[-1]["time"] - rep.gaps[-1]["after"]).total_seconds() <= 20 * bar:
            rep.warnings.append("a gap is within the last 20 candles")

    # abnormal prices in the last bars
    n = len(ordered)
    for i in range(max(1, n - 100), n):
        typical = _typical_range(ordered, i)
        if typical <= 0:
            continue
        x = (ordered[i]["high"] - ordered[i]["low"]) / typical
        if x >= WARN_RANGE_X:
            rep.outliers.append({"time": ordered[i]["time"], "x": round(x, 1)})
            if i >= n - RECENT_BARS:
                if x >= BLOCK_RANGE_X:
                    rep.hard.append(f"extreme candle range x{x:.0f} at "
                                    f"{ordered[i]['time'].strftime('%m-%d %H:%M')} (bad tick?)")
                else:
                    rep.warnings.append(f"strong move x{x:.0f} in the last bars (news or bad tick)")
    return rep


def estimate_server_offset_hours(server_now_ts, utc_now_ts):
    """MT5 server clock minus UTC, rounded to the nearest half hour (for a future MT5 provider)."""
    return round((server_now_ts - utc_now_ts) / 1800) * 0.5


# ===== Fingerprint of the data behind a signal =====

def fingerprint(candles, symbol, tf, source, fetched_at=None, last_n=300):
    """Small record that identifies exactly which candles a signal was computed from."""
    ordered = sorted(candles, key=_ts)[-last_n:]
    h = hashlib.sha256()
    for c in ordered:
        h.update(f"{_ts(c)}|{c['open']:.8g}|{c['high']:.8g}|{c['low']:.8g}|{c['close']:.8g};".encode())
    return {
        "symbol": symbol, "tf": tf, "source": source, "n_bars": len(ordered),
        "first": ordered[0]["time"].strftime("%Y-%m-%d %H:%M") if ordered else None,
        "last": ordered[-1]["time"].strftime("%Y-%m-%d %H:%M") if ordered else None,
        "sha256": h.hexdigest(),
        "fetched_at": datetime.fromtimestamp(fetched_at or time.time(), tz=timezone.utc)
        .strftime("%Y-%m-%d %H:%M:%S"),
    }


def append_jsonl(path, obj):
    """Append one JSON line; returns False (never raises) if the file cannot be written."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


# ===== Providers =====

class FreeFeedProvider:
    """Binance (crypto) and Yahoo (gold, indices) through the existing alert feeds."""
    name = "free-feed"

    def fetch(self, symbol, tf):
        import ict_alerts
        return ict_alerts.get_candles(ict_alerts.INSTRUMENTS[symbol], tf)

    def is_continuous(self, symbol):
        import ict_alerts
        return ict_alerts.INSTRUMENTS[symbol]["source"] == "binance"


class MT5Provider:
    """Not available: needs the MetaTrader5 Python package (Windows). Kept as an explicit stub."""
    name = "mt5"

    def fetch(self, symbol, tf):
        raise NotImplementedError(
            "The MT5 provider needs the MetaTrader5 Python package, which runs on Windows only. "
            "It is not implemented and has never been tested in this project.")

    def is_continuous(self, symbol):
        raise NotImplementedError("MT5 provider is not implemented")


def load_validated(provider, symbol, tf, now_ts=None, min_bars=MIN_BARS):
    """Fetch, validate, fingerprint. Returns (candles_or_None, Report, fingerprint_or_None)."""
    now_ts = int(time.time()) if now_ts is None else now_ts
    candles = provider.fetch(symbol, tf)
    rep = check_candles(candles, tf, now_ts, provider.is_continuous(symbol), min_bars)
    if rep.blocked:
        return None, rep, None
    return candles, rep, fingerprint(candles, symbol, tf, provider.name, now_ts)
