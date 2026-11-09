"""ICT liquidity and SMC sequence engine (pure functions, CLOSED candles only, oldest first).

Liquidity (M15): equal highs/lows, previous day / week levels, previous SESSION high/low,
Asian range, confirmed swing highs/lows. A level is only usable while it is untouched.

Sweep vs breakout:
  sweep    = a candle's WICK trades beyond the level and the same candle CLOSES back inside;
  breakout = a candle CLOSES beyond the level. A wick beyond a level that also closes beyond
             it is a breakout, never a sweep.
A sweep stops being usable ("consumed") when a later candle closes beyond the level (it turned
into a real breakout) or trades beyond the sweep extreme (the sweep failed).

Reversal sequence (bullish; bearish is the mirror):
  1 a valid SSL level exists  2 a confirmed sweep of it  3 a bullish MSS (CHoCH + displacement)
  after the sweep  4 a valid bullish FVG and/or order block created by that move.
The retest and the M5 confirmation are checked by selective.py.

Sessions use fixed UTC windows (Asia 00-07, London 07-13, New York 13-21). Daylight-saving
changes are ignored, so session levels near the boundaries can be an hour off in summer.
"""
import structure as st

SESSIONS = (("Asia", 0, 7), ("London", 7, 13), ("New York", 13, 21))
SWING_K = 3
SEQ_MAX_BARS = 40
SWEEP_LOOKBACK = 60


def session_key(bar):
    t = bar["time"]
    for name, a, b in SESSIONS:
        if a <= t.hour < b:
            return (t.date(), name)
    return (t.date(), "off")


def session_levels(hist, current):
    """High/low of the previous COMPLETED session before `current` (known from `hist` only)."""
    cur = session_key(current)
    blocks = {}
    order = []
    for i, c in enumerate(hist):
        key = session_key(c)
        if key[1] == "off" or key == cur:
            continue
        if key not in blocks:
            blocks[key] = []
            order.append(key)
        blocks[key].append(i)
    if not order:
        return []
    key = order[-1]
    idx = blocks[key]
    hi, lo = max(hist[i]["high"] for i in idx), min(hist[i]["low"] for i in idx)
    end = idx[-1] + 1
    return [
        {"name": f"PSH {key[1]}", "type": "BSL", "price": hi, "scope": "session",
         "swept": st._traded_beyond(hist, end, "BSL", hi)},
        {"name": f"PSL {key[1]}", "type": "SSL", "price": lo, "scope": "session",
         "swept": st._traded_beyond(hist, end, "SSL", lo)}]


def swing_levels(hist, k=SWING_K):
    out = []
    for i, kind, price in st.pivots(hist, k):
        typ = "BSL" if kind == "H" else "SSL"
        out.append({"name": "Swing high" if kind == "H" else "Swing low", "type": typ, "price": price,
                    "scope": "swing", "swept": st._traded_beyond(hist, i + 1, typ, price)})
    return out


def liquidity_map(hist, current, calendar="utc"):
    """All UNTOUCHED liquidity levels known before `current`, strongest scopes first."""
    allv = (st.liquidity_levels(hist, calendar=calendar) + session_levels(hist, current)
            + swing_levels(hist))
    rank = {"external": 0, "session": 1, "internal": 2, "swing": 3}
    out = []
    for lv in sorted(allv, key=lambda v: rank.get(v["scope"], 9)):
        if lv["swept"]:
            continue
        if any(o["type"] == lv["type"] and abs(o["price"] - lv["price"]) < 1e-9 for o in out):
            continue
        out.append(lv)
    return out


def classify_liquidity_event(bar, level):
    """'sweep', 'breakout' or None for one candle against one level dict (needs type, price)."""
    p = level["price"]
    if level["type"] == "BSL":
        if bar["high"] > p:
            return "sweep" if bar["close"] < p else "breakout"
    elif bar["low"] < p:
        return "sweep" if bar["close"] > p else "breakout"
    return None


def find_liquidity_sweeps(bars, lookback=SWEEP_LOOKBACK, calendar="utc"):
    """Confirmed sweeps on the last `lookback` closed bars. Levels for bar i come from bars[:i]."""
    out = []
    for i in range(max(30, len(bars) - lookback), len(bars)):
        c, hist = bars[i], bars[:i]
        done = False
        for lv in liquidity_map(hist, c, calendar):
            if classify_liquidity_event(c, lv) == "sweep":
                out.append({"idx": i, "time": c["time"], "side": "buy" if lv["type"] == "SSL" else "sell",
                            "level": lv["name"], "price": lv["price"], "type": lv["type"],
                            "scope": lv["scope"],
                            "extreme": c["low"] if lv["type"] == "SSL" else c["high"]})
                done = True
                break  # one event per candle: the strongest level
        if done:
            continue
    return out


def sweep_status(bars, sweep):
    """('valid'|'breakout'|'failed', reason). Looks only at candles AFTER the sweep candle."""
    later = bars[sweep["idx"] + 1:]
    if sweep["type"] == "SSL":
        if any(c["close"] < sweep["price"] for c in later):
            return "breakout", "a later candle closed below the swept level (genuine breakout)"
        if any(c["low"] < sweep["extreme"] for c in later):
            return "failed", "price traded below the sweep low again"
    else:
        if any(c["close"] > sweep["price"] for c in later):
            return "breakout", "a later candle closed above the swept level (genuine breakout)"
        if any(c["high"] > sweep["extreme"] for c in later):
            return "failed", "price traded above the sweep high again"
    return "valid", ""


def reversal_sequence(m15, side, fvg_list, ob_list, calendar="utc"):
    """Steps 1-4 of the reversal for `side` ('buy' / 'sell').
    fvg_list / ob_list: validated candidates from selective (only 'valid' ones are used here).
    Returns {'complete', 'stage', 'reasons', 'sweep', 'event', 'zones', 'leg', 'swept_levels'}."""
    want = "bull" if side == "buy" else "bear"
    typ = "SSL" if side == "buy" else "BSL"
    res = {"complete": False, "stage": 0, "reasons": [], "sweep": None, "event": None,
           "zones": [], "leg": None, "event_id": None}
    levels = [lv for lv in liquidity_map(m15[:-1], m15[-1], calendar) if lv["type"] == typ]
    sweeps = [s for s in find_liquidity_sweeps(m15, calendar=calendar) if s["type"] == typ]
    if not levels and not sweeps:
        res["reasons"].append(f"no valid {typ} liquidity level")
        return res
    res["stage"] = 1
    usable = []
    for s in sweeps:
        status, why = sweep_status(m15, s)
        if status == "valid":
            usable.append(s)
        else:
            res["reasons"].append(f"sweep of {s['level']} not usable: {why}")
    if not usable:
        res["reasons"].append(f"no confirmed {typ} sweep yet (a wick alone is not a sweep)")
        return res
    sweep = usable[-1]
    res["sweep"], res["stage"] = sweep, 2
    ev = [e for e in st.structure_events(m15, 2)
          if e["side"] == want and sweep["idx"] < e["idx"] <= sweep["idx"] + SEQ_MAX_BARS]
    mss = [e for e in ev if e["type"] == "CHoCH" and e["mss"]]
    if not mss:
        if any(e["type"] == "CHoCH" for e in ev):
            res["reasons"].append("CHoCH without displacement is not an MSS")
        else:
            res["reasons"].append(f"no {want} MSS/CHoCH after the sweep")
        return res
    event = mss[0]
    res["event"], res["stage"] = event, 3
    zones = [dict(f, kind="FVG") for f in fvg_list if f["valid"] and f["side"] == want
             and f["idx"] > sweep["idx"]]
    zones += [dict(o, kind="OB") for o in ob_list if o["valid"] and o["side"] == want
              and o["break_idx"] == event["idx"]]
    if not zones:
        res["reasons"].append("no valid FVG or order block was created by the MSS move")
        return res
    res["zones"], res["stage"] = zones, 4
    after = m15[event["idx"]:]
    if side == "buy":
        leg_lo, leg_hi = sweep["extreme"], max(c["high"] for c in after)
    else:
        leg_lo, leg_hi = min(c["low"] for c in after), sweep["extreme"]
    eq = (leg_lo + leg_hi) / 2
    res["leg"] = {"low": leg_lo, "high": leg_hi, "eq": eq}
    res["event_id"] = f"{typ}-{sweep['level']}-{round(sweep['price'], 6)}-{int(sweep['time'].timestamp())}"
    res["complete"] = True
    return res


def in_correct_half(zone, leg, side):
    """Premium / discount filter: a BUY zone must sit in the discount half of the leg (below the
    equilibrium), a SELL zone in the premium half. Returns (ok, 'discount'|'premium')."""
    mid = (zone["low"] + zone["high"]) / 2
    half = "discount" if mid <= leg["eq"] else "premium"
    return (half == "discount") == (side == "buy"), half


def d1_context(d1, k=2):
    """('up'|'down'|None, label) from the D1 structure; None when there is not enough data."""
    if not d1 or len(d1) < 20:
        return None, "D1 unavailable"
    trend = st.current_trend(st.structure_events(d1, k))
    return trend, f"D1 {trend}" if trend else "D1 structure unknown"
