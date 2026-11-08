"""Market structure and liquidity engine (phase 2). Pure functions, closed candles only.

`bars` is a list of CLOSED candles, OLDEST FIRST: {'time', 'open', 'high', 'low', 'close'}.
(The live code keeps candles newest first with the forming bar at r[0]; convert with
`closed_bars(r)`.)

No look-ahead / no repainting: a swing pivot of order k is only used k bars AFTER it forms,
and every event at bar t uses data up to bar t only. `test_structure.py` checks that the
events found on a prefix of the data are exactly the first events found on the full data.

Definitions (ICT terms differ between teachers, so these are the explicit rules used):
- Swing high/low: pivot with k lower highs / higher lows on each side.
- BOS: a close beyond the latest swing in the direction of the current trend.
- CHoCH: a close beyond the latest swing AGAINST the current trend (trend flips).
- MSS: a CHoCH whose break candle is also a displacement (body >= 0.8 ATR).
- BSL / SSL: buy-side liquidity above highs (EQH, PDH, PWH, Asian high), sell-side below
  lows (EQL, PDL, PWL, Asian low).
- Sweep: a candle trades beyond an untouched level and CLOSES back inside.
- FVG: three-candle gap; "tested" = price entered it, "filled" = price traded through it.
- Order block: last opposite candle (within 6 bars) before a displacement that breaks structure.
  "mitigated" = price came back into it; "broken" = a candle closed through it. A broken OB
  that price has not closed back through becomes a BREAKER of the opposite side.
- Inducement: a minor swing between the order block and price that is taken before price
  reaches the block.
"""
from datetime import timedelta

DISPLACEMENT_ATR = 0.8
ATR_PERIOD = 14
EQUAL_TOL_ATR = 0.1
FVG_MIN_ATR = 0.3
RETRACE = (0.5, 0.618, 0.705, 0.786)
EXTENSION = (1.0, 1.272, 1.618)
OTE = (0.62, 0.79)


def closed_bars(r):
    """Live series (newest first, r[0] forming) -> closed bars, oldest first."""
    return list(reversed(r[1:]))


def atr_at(bars, t, period=ATR_PERIOD):
    """Average true range of the `period` bars BEFORE bar t (never uses bar t itself)."""
    lo = max(1, t - period)
    trs = [max(bars[i]["high"] - bars[i]["low"], abs(bars[i]["high"] - bars[i - 1]["close"]),
               abs(bars[i]["low"] - bars[i - 1]["close"])) for i in range(lo, t)]
    return sum(trs) / len(trs) if trs else 0.0


def is_displacement(bars, t, mult=DISPLACEMENT_ATR):
    atr = atr_at(bars, t)
    c = bars[t]
    return atr > 0 and abs(c["close"] - c["open"]) >= atr * mult


# ===== Swings and structure =====

def pivots(bars, k):
    """[(index, 'H' or 'L', price)] for pivots that have k bars after them."""
    out = []
    for i in range(k, len(bars) - k):
        h, low = bars[i]["high"], bars[i]["low"]
        if all(h > bars[i - j]["high"] for j in range(1, k + 1)) and \
                all(h >= bars[i + j]["high"] for j in range(1, k + 1)):
            out.append((i, "H", h))
        if all(low < bars[i - j]["low"] for j in range(1, k + 1)) and \
                all(low <= bars[i + j]["low"] for j in range(1, k + 1)):
            out.append((i, "L", low))
    return out


def structure_events(bars, k=2):
    """BOS / CHoCH events: [{'type','side','idx','level','swing_idx','displacement','mss'}]."""
    pv = sorted(pivots(bars, k), key=lambda p: p[0] + k)
    p, trend = 0, None
    last_high = last_low = None
    events = []
    for t in range(len(bars)):
        while p < len(pv) and pv[p][0] + k <= t:  # only pivots already confirmed at bar t
            i, kind, price = pv[p]
            if kind == "H":
                last_high = (i, price)
            else:
                last_low = (i, price)
            p += 1
        close = bars[t]["close"]
        if last_high and close > last_high[1]:
            kind = "CHoCH" if trend == "down" else "BOS"
            disp = is_displacement(bars, t)
            events.append({"type": kind, "side": "bull", "idx": t, "level": last_high[1],
                           "swing_idx": last_high[0], "displacement": disp,
                           "mss": kind == "CHoCH" and disp})
            trend, last_high = "up", None
        elif last_low and close < last_low[1]:
            kind = "CHoCH" if trend == "up" else "BOS"
            disp = is_displacement(bars, t)
            events.append({"type": kind, "side": "bear", "idx": t, "level": last_low[1],
                           "swing_idx": last_low[0], "displacement": disp,
                           "mss": kind == "CHoCH" and disp})
            trend, last_low = "down", None
    return events


def current_trend(events):
    return None if not events else ("up" if events[-1]["side"] == "bull" else "down")


# ===== Liquidity =====

def _day(c):
    return c["time"].date()


def _traded_beyond(bars, start, kind, price):
    """True if any bar from index `start` on traded beyond the level (high above BSL / low below SSL)."""
    for c in bars[start:]:
        if (kind == "BSL" and c["high"] > price) or (kind == "SSL" and c["low"] < price):
            return True
    return False


def liquidity_levels(bars, k=2):
    """Levels known from `bars`: [{'name','type','price','swept','scope'}].
    Uses only completed periods (previous day / week, finished Asian session)."""
    if len(bars) < 3:
        return []
    out = []
    atr = atr_at(bars, len(bars))
    now = bars[-1]["time"]

    def add(name, kind, price, end_idx, scope):
        out.append({"name": name, "type": kind, "price": price, "scope": scope,
                    "swept": _traded_beyond(bars, end_idx, kind, price)})

    # equal highs / lows (internal liquidity): two or more minor pivots within the tolerance
    tol = atr * EQUAL_TOL_ATR
    for kind_p, label, typ in (("H", "EQH", "BSL"), ("L", "EQL", "SSL")):
        pts = [(i, price) for i, kd, price in pivots(bars, k) if kd == kind_p]
        used = set()
        for a in range(len(pts)):
            group = [pts[a]] + [q for q in pts[a + 1:] if abs(q[1] - pts[a][1]) <= tol]
            if len(group) >= 2 and pts[a][0] not in used:
                used.update(i for i, _ in group)
                level = max(p for _, p in group) if typ == "BSL" else min(p for _, p in group)
                add(label, typ, level, group[-1][0] + 1, "internal")

    days = sorted({_day(c) for c in bars})
    today = _day(bars[-1])
    earlier = [d for d in days if d < today]
    if earlier:  # previous trading day
        d = earlier[-1]
        idx = [i for i, c in enumerate(bars) if _day(c) == d]
        add("PDH", "BSL", max(bars[i]["high"] for i in idx), idx[-1] + 1, "external")
        add("PDL", "SSL", min(bars[i]["low"] for i in idx), idx[-1] + 1, "external")
    week = lambda c: c["time"].isocalendar()[:2]
    weeks = sorted({week(c) for c in bars})
    prior_weeks = [w for w in weeks if w < week(bars[-1])]
    if prior_weeks:
        w = prior_weeks[-1]
        idx = [i for i, c in enumerate(bars) if week(c) == w]
        add("PWH", "BSL", max(bars[i]["high"] for i in idx), idx[-1] + 1, "external")
        add("PWL", "SSL", min(bars[i]["low"] for i in idx), idx[-1] + 1, "external")
    asia_day = today if now.hour >= 7 else (earlier[-1] if earlier else None)  # finished session
    if asia_day is not None:
        idx = [i for i, c in enumerate(bars) if _day(c) == asia_day and c["time"].hour < 7]
        if idx:
            add("AsiaH", "BSL", max(bars[i]["high"] for i in idx), idx[-1] + 1, "external")
            add("AsiaL", "SSL", min(bars[i]["low"] for i in idx), idx[-1] + 1, "external")
    return out


def find_sweeps(bars, recent=3):
    """Sweeps on the last `recent` closed bars: a wick beyond an untouched level, close back inside.
    Levels are built from the bars BEFORE the candidate bar, so nothing is looked ahead."""
    out = []
    for i in range(max(3, len(bars) - recent), len(bars)):
        c = bars[i]
        for lv in liquidity_levels(bars[:i]):
            if lv["swept"]:
                continue
            if lv["type"] == "BSL" and c["high"] > lv["price"] and c["close"] < lv["price"]:
                out.append({"idx": i, "side": "sell", "level": lv["name"], "price": lv["price"],
                            "scope": lv["scope"], "type": "BSL"})
            if lv["type"] == "SSL" and c["low"] < lv["price"] and c["close"] > lv["price"]:
                out.append({"idx": i, "side": "buy", "level": lv["name"], "price": lv["price"],
                            "scope": lv["scope"], "type": "SSL"})
    return out


# ===== Dealing range, Premium / Discount, OTE, Fibonacci =====

def dealing_range(bars, k=3):
    """Latest confirmed swing high and low. direction: 'up' if the low came first."""
    pv = pivots(bars, k)
    highs = [p for p in pv if p[1] == "H"]
    lows = [p for p in pv if p[1] == "L"]
    if not highs or not lows:
        return None
    h, low = highs[-1], lows[-1]
    return {"high": h[2], "low": low[2], "direction": "up" if low[0] < h[0] else "down"}


def fib_levels(low, high, direction):
    """Retracement prices (0.5 .. 0.786) and extension targets (1.0, 1.272, 1.618) of a leg."""
    rng = high - low
    if direction == "up":
        ret = {r: high - r * rng for r in RETRACE}
        ext = {e: low + e * rng for e in EXTENSION}
    else:
        ret = {r: low + r * rng for r in RETRACE}
        ext = {e: high - e * rng for e in EXTENSION}
    return {"retracement": ret, "extension": ext}


def premium_discount(bars, k=3):
    """{'zone': 'premium'|'discount'|'equilibrium', 'eq', 'ote': (low, high), ...} or None."""
    dr = dealing_range(bars, k)
    if not dr:
        return None
    rng = dr["high"] - dr["low"]
    if rng <= 0:
        return None
    eq = dr["low"] + rng / 2
    price = bars[-1]["close"]
    band = rng * 0.02
    zone = "equilibrium" if abs(price - eq) <= band else ("premium" if price > eq else "discount")
    if dr["direction"] == "up":   # pullback after an up leg: buy zone near the discount
        ote = (dr["high"] - OTE[1] * rng, dr["high"] - OTE[0] * rng)
    else:
        ote = (dr["low"] + OTE[0] * rng, dr["low"] + OTE[1] * rng)
    return dict(dr, zone=zone, eq=eq, ote=ote, fib=fib_levels(dr["low"], dr["high"], dr["direction"]))


# ===== FVG, order blocks, breakers, inducement =====

def fvgs(bars, include_filled=False):
    """Fair value gaps: [{'side','low','high','idx','status'}] (idx = bar that completed it)."""
    out = []
    for i in range(len(bars) - 2):
        a, c = bars[i], bars[i + 2]
        atr = atr_at(bars, i + 2)
        if atr <= 0:
            continue
        if c["low"] > a["high"] and c["low"] - a["high"] >= atr * FVG_MIN_ATR:
            side, zlow, zhigh = "bull", a["high"], c["low"]
        elif c["high"] < a["low"] and a["low"] - c["high"] >= atr * FVG_MIN_ATR:
            side, zlow, zhigh = "bear", c["high"], a["low"]
        else:
            continue
        status = "valid"
        for later in bars[i + 3:]:
            if side == "bull":
                if later["low"] <= zlow:
                    status = "filled"
                    break
                if later["low"] < zhigh:
                    status = "tested"
            else:
                if later["high"] >= zhigh:
                    status = "filled"
                    break
                if later["high"] > zlow:
                    status = "tested"
        if status != "filled" or include_filled:
            out.append({"side": side, "low": zlow, "high": zhigh, "idx": i + 2, "status": status})
    return out


def order_blocks(bars, events):
    """OB and BREAKER zones built from structure events whose break candle is a displacement.
    [{'kind': 'OB'|'BREAKER', 'side', 'low', 'high', 'idx', 'status'}]"""
    out = []
    for ev in events:
        if not ev["displacement"]:
            continue
        t = ev["idx"]
        want_down = ev["side"] == "bull"  # bullish OB = last DOWN candle before the push up
        j = next((x for x in range(t - 1, max(-1, t - 7), -1)
                  if (bars[x]["close"] < bars[x]["open"]) == want_down
                  and bars[x]["close"] != bars[x]["open"]), None)
        if j is None:
            continue
        zlow, zhigh = bars[j]["low"], bars[j]["high"]
        status, broken_at = "valid", None
        for x in range(t + 1, len(bars)):
            c = bars[x]
            if ev["side"] == "bull":
                if c["close"] < zlow:
                    status, broken_at = "broken", x
                    break
                if c["low"] <= zhigh:
                    status = "mitigated"
            else:
                if c["close"] > zhigh:
                    status, broken_at = "broken", x
                    break
                if c["high"] >= zlow:
                    status = "mitigated"
        out.append({"kind": "OB", "side": ev["side"], "low": zlow, "high": zhigh, "idx": j,
                    "status": status})
        if status == "broken":  # polarity flips: stays valid while price has not closed back through
            opposite = "bear" if ev["side"] == "bull" else "bull"
            back = any((c["close"] > zhigh) if ev["side"] == "bull" else (c["close"] < zlow)
                       for c in bars[broken_at + 1:])
            out.append({"kind": "BREAKER", "side": opposite, "low": zlow, "high": zhigh,
                        "idx": j, "status": "invalid" if back else "valid"})
    return out


def inducement(bars, ob):
    """True when a minor swing between the block and price was taken before price reached the block."""
    start = ob["idx"] + 1
    for i, kind, price in pivots(bars, 1):
        if i < start:
            continue
        if ob["side"] == "bull" and kind == "L" and price > ob["high"]:
            if any(c["low"] < price for c in bars[i + 2:]):
                return True
        if ob["side"] == "bear" and kind == "H" and price < ob["low"]:
            if any(c["high"] > price for c in bars[i + 2:]):
                return True
    return False
