"""Classical confirmations and a standalone classical setup.

Works on the same candle series as the ICT logic: a list of dicts with
open/high/low/close, newest first, where r[0] is the bar still forming and
r[1:] are closed bars. Pure Python, no network.
"""
from ict_smc_bot import get_atr


# ===== Trend =====

def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def trend(r, cfg):
    """'up', 'down', 'flat', or None when there are not enough candles."""
    closes = [c["close"] for c in reversed(r[1:])]
    fast = ema(closes, cfg.ema_fast)
    slow = ema(closes, cfg.ema_slow)
    if fast is None or slow is None:
        return None
    last = closes[-1]
    if last > fast > slow:
        return "up"
    if last < fast < slow:
        return "down"
    return "flat"


# ===== Swing levels (support and resistance) =====

def is_pivot_high(r, i, k):
    h = r[i]["high"]
    return (all(h > r[i - j]["high"] for j in range(1, k + 1))
            and all(h >= r[i + j]["high"] for j in range(1, k + 1)))


def is_pivot_low(r, i, k):
    low = r[i]["low"]
    return (all(low < r[i - j]["low"] for j in range(1, k + 1))
            and all(low <= r[i + j]["low"] for j in range(1, k + 1)))


def swing_levels(r, cfg, skip=4):
    """Prices of earlier swing highs and lows, ignoring the newest `skip` bars."""
    k = cfg.sr_pivot
    n = min(len(r), cfg.sr_lookback + skip)
    levels = []
    for i in range(skip + k, n - k):
        if is_pivot_high(r, i, k):
            levels.append(r[i]["high"])
        if is_pivot_low(r, i, k):
            levels.append(r[i]["low"])
    return levels


# ===== Fibonacci =====

def fib_zone(side, leg_low, leg_high, cfg):
    """(low, high) prices of the retracement zone between fib_shallow and fib_deep."""
    rng = leg_high - leg_low
    if side == "buy":
        return leg_high - cfg.fib_deep * rng, leg_high - cfg.fib_shallow * rng
    return leg_low + cfg.fib_shallow * rng, leg_low + cfg.fib_deep * rng


# ===== Candle patterns =====

def _parts(c):
    rng = c["high"] - c["low"]
    body = abs(c["close"] - c["open"])
    lower = min(c["open"], c["close"]) - c["low"]
    upper = c["high"] - max(c["open"], c["close"])
    return rng, body, lower, upper


def bullish_engulfing(older, newer):
    return (older["close"] < older["open"] and newer["close"] > newer["open"]
            and newer["open"] <= older["close"] and newer["close"] >= older["open"])


def bearish_engulfing(older, newer):
    return (older["close"] > older["open"] and newer["close"] < newer["open"]
            and newer["open"] >= older["close"] and newer["close"] <= older["open"])


def hammer(c):
    rng, body, lower, _ = _parts(c)
    return rng > 0 and lower >= 0.6 * rng and body <= 0.3 * rng


def shooting_star(c):
    rng, body, _, upper = _parts(c)
    return rng > 0 and upper >= 0.6 * rng and body <= 0.3 * rng


# ===== Confirmations for an ICT setup =====

def ict_confluence(r, cfg, setup):
    """Classical confirmations that agree with an ICT setup, as a list of names.

    trend:  the EMA trend points the same way as the trade
    fib:    the entry sits in the fib zone of the sweep-to-displacement leg
    level:  the entry is near an earlier swing high or low
    candle: engulfing or pin bar on the sweep/displacement candles
    """
    side, entry = setup["side"], setup["entry"]
    buy = side == "buy"
    atr = get_atr(r, cfg.atr_period)
    points = []

    if trend(r, cfg) == ("up" if buy else "down"):
        points.append("trend")

    leg_low, leg_high = (r[2]["low"], r[1]["high"]) if buy else (r[1]["low"], r[2]["high"])
    if leg_high > leg_low:
        zone_low, zone_high = fib_zone(side, leg_low, leg_high, cfg)
        if zone_low <= entry <= zone_high:
            points.append("fib")

    tol = cfg.sr_tol_atr * atr
    if any(abs(entry - level) <= tol for level in swing_levels(r, cfg)):
        points.append("level")

    if buy:
        candle = bullish_engulfing(r[2], r[1]) or hammer(r[1]) or hammer(r[2])
    else:
        candle = bearish_engulfing(r[2], r[1]) or shooting_star(r[1]) or shooting_star(r[2])
    if candle:
        points.append("candle")

    return points


# ===== Standalone classical setup =====

def find_classic_setup(r, cfg):
    """Trend pullback: with the trend, price retraces into the fib zone of the last
    swing leg and prints a reversal candle. Returns {'side','entry','sl','tp'} or None.
    Entry is the close of the reversal candle, meant as a limit order at that price."""
    if len(r) < max(cfg.ema_slow + 10, cfg.leg_lookback + cfg.leg_span + cfg.sr_pivot + 2):
        return None
    t = trend(r, cfg)
    if t not in ("up", "down"):
        return None
    atr = get_atr(r, cfg.atr_period)
    if atr <= 0:
        return None

    k = cfg.sr_pivot
    buy = t == "up"
    pivot = None
    for i in range(1 + k, cfg.leg_lookback + 1):
        if (is_pivot_high(r, i, k) if buy else is_pivot_low(r, i, k)):
            pivot = i
            break
    if pivot is None:
        return None

    before = r[pivot + 1: pivot + 1 + cfg.leg_span]
    if buy:
        leg_high = r[pivot]["high"]
        leg_low = min(c["low"] for c in before)
        if max(c["high"] for c in r[1:pivot]) > leg_high:
            return None  # the swing high was already exceeded
    else:
        leg_low = r[pivot]["low"]
        leg_high = max(c["high"] for c in before)
        if min(c["low"] for c in r[1:pivot]) < leg_low:
            return None
    if leg_high - leg_low < cfg.leg_min_atr * atr:
        return None

    zone_low, zone_high = fib_zone("buy" if buy else "sell", leg_low, leg_high, cfg)
    c1, c2 = r[1], r[2]

    if buy:
        pullback = min(c1["low"], c2["low"])
        if not (zone_low <= pullback <= zone_high):
            return None
        if not (bullish_engulfing(c2, c1) or hammer(c1)):
            return None
        entry = c1["close"]
        sl = pullback - atr * cfg.sl_buffer_atr
        if sl >= entry:
            return None
        return {"side": "buy", "entry": entry, "sl": sl,
                "tp": entry + (entry - sl) * cfg.rr}

    pullback = max(c1["high"], c2["high"])
    if not (zone_low <= pullback <= zone_high):
        return None
    if not (bearish_engulfing(c2, c1) or shooting_star(c1)):
        return None
    entry = c1["close"]
    sl = pullback + atr * cfg.sl_buffer_atr
    if sl <= entry:
        return None
    return {"side": "sell", "entry": entry, "sl": sl,
            "tp": entry - (sl - entry) * cfg.rr}
