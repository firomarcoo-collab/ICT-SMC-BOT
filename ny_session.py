"""New York session intelligence (phase 1: it only DESCRIBES the market, it never decides a trade).

Everything uses America/New_York wall-clock time, so daylight saving changes move the UTC hours by itself, and closed
M15 candles only (the candle that is still forming is never used).

Levels  : Asia high/low (19:00-03:00 ET), London high/low (03:00-09:30 ET), previous trading day high/low (17:00 ET roll),
          New York opening range (first 30 minutes from 09:30 ET = two M15 candles).
Scenarios (a sweep alone is NOT a reversal: it needs a close-based structure break with displacement afterwards):
  1 buy-side sweep -> bearish reversal      2 sell-side sweep -> bullish reversal
  3 London High sweep   4 London Low sweep   5 Previous Day High sweep   6 Previous Day Low sweep
  7 Opening Range Breakout   8 Failed Breakout   9 Judas Swing   10 Trend continuation   11 New York reversal
"""
from datetime import datetime, time as dtime, timedelta, timezone

import structure as st

NY_OPEN = dtime(9, 30)
NY_CLOSE = dtime(17, 0)
OR_BARS = 2                       # two M15 candles = the 30 minute opening range
JUDAS_BARS = 4                    # the first hour after the open
MIN_LEVEL_BARS = 3                # a session level needs at least this many candles behind it
MIN_PRIOR_BARS = 20
NAMES = {1: "buy-side sweep -> bearish reversal", 2: "sell-side sweep -> bullish reversal", 3: "London High sweep",
         4: "London Low sweep", 5: "Previous Day High sweep", 6: "Previous Day Low sweep",
         7: "Opening Range Breakout", 8: "Failed Breakout", 9: "Judas Swing", 10: "Trend continuation",
         11: "New York reversal"}


def _et(ts):
    return st._ny_local(datetime.fromtimestamp(ts, tz=timezone.utc))


def _local(bar):
    return st._ny_local(bar["time"])


def _window(bars, start, end):
    """Closed bars whose New York time lies in [start, end)."""
    return [b for b in bars if start <= _local(b).replace(tzinfo=None) < end]


def phase(now_ts):
    """Which part of the day it is, in New York time."""
    t = _et(now_ts)
    if t.weekday() >= 5:
        return "Weekend"
    hm = t.time()
    if dtime(19, 0) <= hm or hm < dtime(3, 0):
        return "Asia"
    if hm < dtime(8, 0):
        return "London"
    if hm < NY_OPEN:
        return "London / New York pre-open"
    if hm < dtime(11, 30):
        return "New York open"
    if hm < NY_CLOSE:
        return "New York"
    return "After hours"


def _rng(bars, need=MIN_LEVEL_BARS):
    if len(bars) < need:
        return None
    return {"high": max(b["high"] for b in bars), "low": min(b["low"] for b in bars), "bars": len(bars)}


def levels(m15, now_ts):
    """Asia / London / previous-day / opening-range levels known at now_ts (None where there is not enough data)."""
    now = _et(now_ts).replace(tzinfo=None)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    open_dt = today.replace(hour=9, minute=30)
    ref = open_dt if now >= open_dt else today.replace(hour=0)
    asia = _rng(_window(m15, today - timedelta(hours=5), today + timedelta(hours=3)))        # 19:00 yesterday - 03:00
    london_end = min(now, open_dt)
    london = _rng(_window(m15, today + timedelta(hours=3), london_end)) if london_end > today + timedelta(hours=3) else None
    roll = today + timedelta(hours=17) if now.hour >= 17 else today - timedelta(hours=7)      # last 17:00 ET roll
    pd = None
    for back in range(0, 4):                                                                   # skip weekend gaps
        pd = _rng(_window(m15, roll - timedelta(hours=24 * (back + 1)), roll - timedelta(hours=24 * back)))
        if pd:
            break
    orange = None
    if now >= open_dt + timedelta(minutes=15 * OR_BARS):
        orange = _rng(_window(m15, open_dt, open_dt + timedelta(minutes=15 * OR_BARS)), OR_BARS)
    return {"asia": asia, "london": london, "prev_day": pd, "opening_range": orange, "open": open_dt, "ref": ref}


def _sweeps(bars, lv_price, side, start):
    """First sweep of a level from index `start`: a candle whose wick goes beyond the level and whose CLOSE is back inside.
    A candle that closes beyond the level is a breakout, not a sweep. Returns {'idx', 'extreme'} or None."""
    for i in range(start, len(bars)):
        b = bars[i]
        if side == "high":
            if b["high"] <= lv_price:
                continue
            return {"idx": i, "extreme": b["high"]} if b["close"] < lv_price else None
        if b["low"] >= lv_price:
            continue
        return {"idx": i, "extreme": b["low"]} if b["close"] > lv_price else None
    return None


def _confirm(bars, after_idx, want):
    """First close-based structure break in direction `want` ('bull'/'bear') with displacement after after_idx."""
    for e in st.structure_events(bars, 2):
        if e["side"] == want and e["idx"] > after_idx and e["displacement"]:
            return e
    return None


def analyze(m15, now_ts, h1_trend=None):
    """The New York picture from closed M15 candles. h1_trend: 'up' / 'down' / None (only used for continuation).
    Returns {'phase','levels','scenarios','notes'}; scenarios is a list of dicts (see _scenario)."""
    out = {"phase": phase(now_ts), "levels": {}, "scenarios": [], "notes": []}
    bars = [b for b in m15 if int(b["time"].timestamp()) + 900 <= now_ts]
    if len(bars) < MIN_PRIOR_BARS:
        out["notes"].append("not enough M15 candles for the New York analysis")
        return out
    lv = levels(bars, now_ts)
    out["levels"] = {k: v for k, v in lv.items() if k in ("asia", "london", "prev_day", "opening_range")}
    now = _et(now_ts).replace(tzinfo=None)
    open_dt = lv["open"]
    if now < open_dt or now.time() >= NY_CLOSE or _et(now_ts).weekday() >= 5:
        return out
    # candles from the open on; structure uses everything before it for the pivots
    first = next((i for i, b in enumerate(bars) if _local(b).replace(tzinfo=None) >= open_dt), None)
    if first is None:
        out["notes"].append("the first New York candle has not closed yet")
        return out
    today = bars[first:]
    london = lv["london"]
    pre_dir = None
    if london:
        lb = _window(bars, open_dt.replace(hour=3, minute=0), open_dt)
        if lb:
            pre_dir = "up" if lb[-1]["close"] > lb[0]["open"] else "down"
    targets = []
    if london:
        targets += [("London High", london["high"], "high", 3), ("London Low", london["low"], "low", 4)]
    if lv["prev_day"]:
        targets += [("Previous Day High", lv["prev_day"]["high"], "high", 5), ("Previous Day Low", lv["prev_day"]["low"], "low", 6)]
    if lv["asia"]:
        targets += [("Asia High", lv["asia"]["high"], "high", None), ("Asia Low", lv["asia"]["low"], "low", None)]
    reversal = False
    for name, price, side, special in targets:
        sw = _sweeps(bars, price, side, first)
        if not sw:
            continue
        want = "bear" if side == "high" else "bull"
        ev = _confirm(bars, sw["idx"], want)
        ids = [1 if side == "high" else 2] + ([special] if special else [])
        move = "bearish" if want == "bear" else "bullish"
        rec = {"ids": ids, "name": f"{name} sweep", "level": price, "side": "sell" if want == "bear" else "buy",
               "extreme": sw["extreme"], "confirmed": bool(ev)}
        if ev:
            reversal = True
            rec["event"] = ("MSS" if ev["type"] == "CHoCH" else "BOS") + " with displacement"
            if lv["open"] and sw["idx"] - first < JUDAS_BARS:
                open_px = today[0]["open"]
                if (want == "bear" and bars[-1]["close"] < open_px) or (want == "bull" and bars[-1]["close"] > open_px):
                    rec["ids"].append(9)
            if pre_dir and ((pre_dir == "up" and want == "bear") or (pre_dir == "down" and want == "bull")):
                rec["ids"].append(11)
            rec["text"] = f"{name} swept and a {move} {rec['event']} followed: {move} reversal CONFIRMED."
        else:
            rec["text"] = (f"{name} swept (wick through {price:.5g}, close back inside) but there is no {move} structure break yet: "
                           "UNCONFIRMED, a sweep alone is not a reversal.")
        out["scenarios"].append(rec)
    orr = lv["opening_range"]
    if orr:
        after = bars[first + OR_BARS:]
        broke = None
        for i, b in enumerate(after):
            if b["close"] > orr["high"]:
                broke = ("buy", i)
            elif b["close"] < orr["low"]:
                broke = ("sell", i)
            if broke:
                break
        if broke:
            side, i = broke
            later = after[i + 1:]
            back_in = any(orr["low"] <= b["close"] <= orr["high"] for b in later)
            if back_in:
                out["scenarios"].append({"ids": [8], "name": "Failed Breakout", "side": "sell" if side == "buy" else "buy",
                                         "level": orr["high"] if side == "buy" else orr["low"], "confirmed": True,
                                         "text": f"The {side.upper()} break of the opening range failed: price closed back inside it."})
            else:
                out["scenarios"].append({"ids": [7], "name": "Opening Range Breakout", "side": side, "confirmed": True,
                                         "level": orr["high"] if side == "buy" else orr["low"],
                                         "text": f"Opening range broken by a close ({side.upper()}), price has not come back inside."})
    if not reversal and h1_trend in ("up", "down"):
        want = "bull" if h1_trend == "up" else "bear"
        for e in st.structure_events(bars, 2):
            if e["side"] == want and e["type"] == "BOS" and e["displacement"] and e["idx"] >= first:
                out["scenarios"].append({"ids": [10], "name": "Trend continuation", "side": "buy" if want == "bull" else "sell",
                                         "confirmed": True, "level": e["level"],
                                         "text": f"BOS with displacement in the direction of the H1 trend ({h1_trend})."})
                break
    return out


def lines(res, digits=2):
    """Short text lines for the alert / scan output."""
    f = lambda v: f"{v:.{digits}f}"                                                    # noqa: E731
    out = [f"NY SESSION: {res['phase']}"]
    lv = res.get("levels") or {}
    for key, label in (("asia", "Asia"), ("london", "London"), ("prev_day", "Previous day"), ("opening_range", "NY opening range")):
        if lv.get(key):
            out.append(f"{label}: high {f(lv[key]['high'])} / low {f(lv[key]['low'])}")
    for s in res.get("scenarios", []):
        out.append(f"{'CONFIRMED' if s['confirmed'] else 'UNCONFIRMED'} [{','.join(str(i) for i in s['ids'])}] {s['text']}")
    return out


def summary(res):
    """One short phrase for a scan line, or '' when there is nothing to say."""
    sc = [s for s in res.get("scenarios", []) if s.get("confirmed")] or res.get("scenarios", [])
    if not sc:
        return ""
    s = max(sc, key=lambda x: len(x["ids"]))
    return f"NY: {s['name']} ({'confirmed' if s['confirmed'] else 'unconfirmed'})"


def us_window_closed(now_ts, spec=None):
    """US100 / US30 are only reviewed in the New York open window (US_NY_WINDOW, default 09:30-12:00 New York time, DST-aware).
    Returns '' when inside the window, otherwise the reason. US_MODE=1 turns the gate on (default off: any hour)."""
    import os
    if os.environ.get("US_MODE", "0") != "1":
        return ""
    spec = spec or os.environ.get("US_NY_WINDOW") or "09:30-12:00"
    try:
        a, b = spec.split("-")
        start = dtime(*[int(x) for x in a.split(":")])
        end = dtime(*[int(x) for x in b.split(":")])
    except (ValueError, TypeError):
        raise SystemExit(f"US_NY_WINDOW must look like 09:30-12:00 (New York time), got {spec!r}")
    t = _et(now_ts)
    if t.weekday() >= 5:
        return "weekend"
    if start <= t.time() < end:
        return ""
    return f"outside the New York open window {spec} (now {t.strftime('%H:%M')} New York)"
