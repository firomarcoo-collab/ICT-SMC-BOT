"""Volatility engine: ATR regime, abnormal candles, spread monitor and a dynamic stop-distance check. Signals only; it never places an order.
Rules: only CLOSED candles (oldest first, dicts with high / low / close). Unknown spread is reported as unknown, never as a pass or a failure
by itself. An extreme regime, an abnormal last candle or a known spread that eats too much of the stop vetoes a confirmation."""
from __future__ import annotations

import os

PERIOD = 14
BASE_BARS = 100                 # the 'normal' ATR is the median ATR of the previous 100 candles
LOW, HIGH, EXTREME = 0.6, 1.5, 2.5      # ATR now / normal ATR
ABNORMAL_RANGE_ATR = 3.0        # last candle range above 3 x ATR = abnormal (news spike, gap)
MAX_SPREAD_OF_RISK = 0.25       # spread above 25% of the stop distance is too expensive
MIN_SL_ATR = {"low": 1.0, "normal": 1.0, "high": 1.5, "extreme": 2.0}


def enabled():
    return os.environ.get("VOLATILITY_GATE", "1") != "0"


def _tr(bars, i):
    b = bars[i]
    prev = bars[i - 1]["close"] if i > 0 else b["close"]
    return max(b["high"] - b["low"], abs(b["high"] - prev), abs(b["low"] - prev))


def atr_series(bars, period=PERIOD):
    """ATR value after each candle (simple mean of the last `period` true ranges); None until enough candles."""
    trs = [_tr(bars, i) for i in range(len(bars))]
    return [None if i + 1 < period else sum(trs[i + 1 - period:i + 1]) / period for i in range(len(bars))]


def regime(bars):
    """{'atr', 'normal', 'ratio', 'regime'}; regime is 'unknown' without enough candles."""
    s = atr_series(bars)
    if len(bars) < PERIOD + 20 or s[-1] is None:
        return {"atr": None, "normal": None, "ratio": None, "regime": "unknown"}
    hist = sorted(x for x in s[-BASE_BARS - 1:-1] if x)
    normal = hist[len(hist) // 2] if hist else None
    if not normal:
        return {"atr": s[-1], "normal": None, "ratio": None, "regime": "unknown"}
    ratio = s[-1] / normal
    name = "low" if ratio < LOW else "normal" if ratio <= HIGH else "high" if ratio <= EXTREME else "extreme"
    return {"atr": s[-1], "normal": normal, "ratio": round(ratio, 2), "regime": name}


def abnormal_candle(bars):
    """True when the last closed candle's range is above 3 x the ATR measured BEFORE it."""
    s = atr_series(bars)
    if len(bars) < PERIOD + 2 or s[-2] is None:
        return False
    return (bars[-1]["high"] - bars[-1]["low"]) > ABNORMAL_RANGE_ATR * s[-2]


def spread_check(spread, risk):
    """(status, note): status is 'ok', 'wide' or 'unknown'. Compares the spread with the stop distance."""
    if spread is None or not risk or risk <= 0:
        return "unknown", "spread unknown (not counted for or against the trade)"
    share = spread / risk
    if share > MAX_SPREAD_OF_RISK:
        return "wide", f"spread is {share * 100:.0f}% of the stop distance (limit {MAX_SPREAD_OF_RISK * 100:.0f}%)"
    return "ok", f"spread is {share * 100:.0f}% of the stop distance"


def min_sl_distance(atr, name):
    """The smallest sensible stop distance for the regime (advice, in price units)."""
    return None if atr is None else atr * MIN_SL_ATR.get(name, 1.0)


def assess(bars, spread=None, risk=None):
    """{ok, regime, ratio, problems, notes, min_sl}. ok=False means veto. Unknown data never vetoes by itself."""
    out = {"ok": True, "regime": "unknown", "ratio": None, "problems": [], "notes": [], "min_sl": None}
    rg = regime(bars or [])
    out["regime"], out["ratio"] = rg["regime"], rg["ratio"]
    if rg["regime"] == "unknown":
        out["notes"].append("volatility regime unknown (not enough candles)")
    elif rg["regime"] == "extreme":
        out["problems"].append(f"extreme volatility: ATR is {rg['ratio']}x its normal level")
    elif rg["regime"] == "high":
        out["notes"].append(f"high volatility: ATR is {rg['ratio']}x normal, use the wider stop")
    elif rg["regime"] == "low":
        out["notes"].append(f"low volatility: ATR is {rg['ratio']}x normal, targets may be slow")
    if bars and abnormal_candle(bars):
        out["problems"].append("abnormal last candle (range above 3 x ATR): wait for the market to settle")
    out["min_sl"] = min_sl_distance(rg["atr"], rg["regime"])
    if risk and out["min_sl"] and risk < out["min_sl"] * 0.8:
        out["notes"].append(f"stop distance {risk:.5g} is tighter than the {rg['regime']}-regime guide {out['min_sl']:.5g} (advice only)")
    st, note = spread_check(spread, risk)
    (out["problems"] if st == "wide" else out["notes"]).append(note)
    out["ok"] = not out["problems"]
    return out
