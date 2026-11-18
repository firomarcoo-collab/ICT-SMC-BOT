"""Market-state engine (phase 2, display only: it describes what stage the market is in, it never creates a signal).

States, strongest evidence first:
REVERSAL (sweep followed by an opposite structure break with displacement) > LIQUIDITY SWEEP (wick through a level, close
back inside, no break yet) > DISPLACEMENT (a large candle just printed) > RETEST (price is back inside the fresh FVG / IFVG /
OB it left) > TRENDING (ADX 25+ and structure agree) > ACCUMULATION / DISTRIBUTION (a quiet range after a down / up trend,
ADX below 20, shrinking volatility) > RANGING (ADX below 20) > WAITING FOR CONFIRMATION (nothing clear).
Closed candles only. The thresholds are plain constants at the top, not tuned on any results.
"""
import indicators
import structure as st

STATES = ("TRENDING", "RANGING", "ACCUMULATION", "DISTRIBUTION", "LIQUIDITY SWEEP", "DISPLACEMENT", "RETEST", "REVERSAL",
          "WAITING FOR CONFIRMATION")
ADX_TREND = 25.0
ADX_RANGE = 20.0
SHRINK = 0.8                   # recent volatility below 80% of the one before it = contraction
RECENT = 3                     # how many closed candles back a sweep / displacement still counts
RETEST_AGE = 30                # a zone older than this many candles is not a retest any more
MIN_BARS = 60


def _atr_avg(bars, a, b):
    seg = bars[a:b]
    return sum(c["high"] - c["low"] for c in seg) / len(seg) if seg else 0.0


def _retest(bars):
    """The newest fresh zone (FVG / IFVG / OB) that price is inside right now, or None."""
    last = bars[-1]
    zones = [dict(z, kind="FVG") for z in st.fvgs(bars) if z["status"] in ("valid", "tested")]
    zones += [z for z in st.ifvgs(bars) if z["status"] in ("valid", "tested")]
    zones += [z for z in st.order_blocks(bars, st.structure_events(bars, 2)) if z["kind"] == "OB" and z["status"] in ("valid", "mitigated")]
    best = None
    for z in zones:
        if len(bars) - 1 - z["idx"] > RETEST_AGE or z["idx"] >= len(bars) - 1:
            continue
        if last["low"] <= z["high"] and last["high"] >= z["low"]:
            if best is None or z["idx"] > best["idx"]:
                best = z
    return best


def classify(m15, h1_trend=None, calendar="utc"):
    """-> {'state', 'detail', 'trend', 'adx', 'zone'}. m15: closed M15 candles, oldest first."""
    res = {"state": "WAITING FOR CONFIRMATION", "detail": "", "trend": None, "adx": None, "zone": None}
    if len(m15) < MIN_BARS:
        res["detail"] = f"only {len(m15)} closed candles ({MIN_BARS} needed)"
        return res
    ev = st.structure_events(m15, 2)
    trend = st.current_trend(ev)
    res["trend"] = trend
    a = indicators.adx(m15)
    adx = a[0] if a else None
    res["adx"] = adx
    n = len(m15)
    # 1/2: a sweep of the last few candles, and whether an opposite break with displacement followed it
    sweeps = st.find_sweeps(m15, recent=RECENT * 4, calendar=calendar)
    for sw in reversed(sweeps):
        want = "bull" if sw["side"] == "buy" else "bear"
        if any(e["side"] == want and e["idx"] > sw["idx"] and e["displacement"] for e in ev):
            res.update(state="REVERSAL", detail=f"{sw['level']} swept, then a {want} structure break with displacement")
            return res
    for sw in reversed(sweeps):
        if n - 1 - sw["idx"] < RECENT:
            res.update(state="LIQUIDITY SWEEP", detail=f"{sw['level']} swept, no structure break yet")
            return res
    # 3: a large candle among the last few
    for i in range(n - RECENT, n):
        if st.is_displacement(m15, i):
            res.update(state="DISPLACEMENT", detail="a displacement candle just closed")
            return res
    # 4: back inside a fresh zone
    z = _retest(m15)
    if z:
        res.update(state="RETEST", detail=f"price is back in a {z['kind']} {z['low']:.5g}-{z['high']:.5g}", zone=z)
        return res
    # 5: trend / range / accumulation / distribution
    if adx is not None and adx >= ADX_TREND and trend in ("up", "down"):
        res.update(state="TRENDING", detail=f"ADX {adx:.0f} and structure {trend}")
        return res
    if adx is not None and adx < ADX_RANGE:
        before, now = _atr_avg(m15, n - 20, n - 10), _atr_avg(m15, n - 10, n)
        pd = st.premium_discount(m15)
        if before and now < SHRINK * before and pd:
            if trend == "down" or (h1_trend == "down" and pd["zone"] == "discount"):
                res.update(state="ACCUMULATION", detail=f"quiet range in the discount after a down move (ADX {adx:.0f})")
                return res
            if trend == "up" or (h1_trend == "up" and pd["zone"] == "premium"):
                res.update(state="DISTRIBUTION", detail=f"quiet range in the premium after an up move (ADX {adx:.0f})")
                return res
        res.update(state="RANGING", detail=f"ADX {adx:.0f}, no trend")
        return res
    res["detail"] = "no clear stage"
    return res


def line(res):
    return f"{res['state']}" + (f" ({res['detail']})" if res.get("detail") else "")
