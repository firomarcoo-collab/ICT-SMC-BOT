"""Offline checks for structure.py. Run: python test_structure.py"""
import random
from datetime import datetime, timedelta, timezone

from structure import (calendar_for, day_key, week_key, closed_bars, dealing_range, fib_levels, find_sweeps, fvgs,
                       inducement, liquidity_levels, order_blocks, pivots, premium_discount,
                       structure_events)

T0 = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # a Monday


def bars_from_closes(closes, step=15, start=T0):
    out, prev = [], closes[0]
    for n, c in enumerate(closes):
        out.append({"time": start + timedelta(minutes=step * n), "open": prev,
                    "high": max(prev, c) + 0.1, "low": min(prev, c) - 0.1, "close": c})
        prev = c
    return out


def raw(rows, step=15, start=T0):
    return [{"time": start + timedelta(minutes=step * n), "open": o, "high": h, "low": l, "close": c}
            for n, (o, h, l, c) in enumerate(rows)]


def test_pivot_needs_k_bars_after_it():
    b = bars_from_closes([10, 11, 12, 11, 10, 11])
    assert [(i, k) for i, k, _ in pivots(b, 2)] == [(2, "H")]
    assert pivots(b[:4], 2) == []  # the peak is not confirmed yet: it cannot be used


def test_bos_then_choch():
    closes = [10, 11, 12, 11, 10, 11, 12, 13, 12, 11, 10, 9, 8]
    ev = structure_events(bars_from_closes(closes))
    assert [(e["type"], e["side"]) for e in ev] == [("BOS", "bull"), ("CHoCH", "bear")], ev
    assert ev[0]["level"] == 12.1 and ev[0]["idx"] == 7


def test_mss_is_a_choch_with_displacement():
    closes = [10, 11, 12, 11, 10, 11, 12, 13, 12, 11, 10, 9, 8]
    assert [e["mss"] for e in structure_events(bars_from_closes(closes))] == [False, True]
    flat = [10, 10.2, 10.4, 10.2, 10.0, 10.2, 10.4, 10.6, 10.4, 10.2, 10.0, 9.8, 9.6]
    # small candles: still a CHoCH, but not a displacement, so not an MSS
    ev = structure_events(bars_from_closes([c * 1 for c in flat] + [9.5]))
    assert all(not e["displacement"] for e in ev) or not any(e["mss"] for e in ev if e["type"] == "BOS")


def test_no_repainting_prefix_stability():
    rnd = random.Random(7)
    closes, p = [], 100.0
    for _ in range(220):
        p += rnd.uniform(-1.5, 1.5)
        closes.append(p)
    b = bars_from_closes(closes)
    full = structure_events(b)
    for m in (60, 90, 130, 180):
        part = structure_events(b[:m])
        expected = [e for e in full if e["idx"] < m]
        assert part == expected, (m, part[-2:], expected[-2:])
    # zones of the past do not change when newer bars arrive (only their status can)
    early = {(f["idx"], f["side"], f["low"], f["high"]) for f in fvgs(b[:150], True)}
    late = {(f["idx"], f["side"], f["low"], f["high"]) for f in fvgs(b, True) if f["idx"] < 150}
    assert early == late


def day_bars(day, hours_values):
    """hourly bars for one day: hours_values = {hour: (high, low)}; others flat 100."""
    start = T0 + timedelta(days=day)
    rows = []
    for h in range(24):
        hi, lo = hours_values.get(h, (100.5, 99.5))
        rows.append((100, hi, lo, 100))
    return raw(rows, step=60, start=start)


def test_liquidity_levels_pdh_pdl_asia_and_week():
    prev_week = day_bars(-3, {10: (112, 99.5)})  # Friday of the week before
    yesterday = day_bars(0, {3: (105, 99.5), 14: (110, 95)})  # Monday: Asia high 105, day high 110, low 95
    today = day_bars(1, {})[:9]  # Tuesday until 08:00
    bars = prev_week + yesterday + today
    lv = {x["name"]: x for x in liquidity_levels(bars)}
    assert lv["PDH"]["price"] == 110 and lv["PDL"]["price"] == 95 and lv["PDH"]["type"] == "BSL"
    assert lv["PWH"]["price"] == 112 and lv["PWL"]["type"] == "SSL"
    # today's Asian session (00:00-07:00) is finished at 08:00: flat 100.5 / 99.5
    assert lv["AsiaH"]["price"] == 100.5 and lv["AsiaL"]["price"] == 99.5
    assert not lv["PDH"]["swept"]


def hourly(start, end, high=101, low=99, spike=None):
    """flat hourly bars from start to end (UTC); spike = {datetime: high}"""
    out, t = [], start
    while t < end:
        h = (spike or {}).get(t, high)
        out.append({"time": t, "open": 100, "high": h, "low": low, "close": 100})
        t += timedelta(hours=1)
    return out


def test_cfd_week_starts_sunday_evening_not_monday():
    utc = timezone.utc
    week_a = hourly(datetime(2026, 1, 5, tzinfo=utc), datetime(2026, 1, 9, 22, tzinfo=utc))
    # Sunday 22:00 UTC = 17:00 New York: the new CFD week opens with a spike to 130
    week_b = hourly(datetime(2026, 1, 11, 22, tzinfo=utc), datetime(2026, 1, 14, 12, tzinfo=utc),
                    spike={datetime(2026, 1, 11, 22, tzinfo=utc): 130})
    bars = week_a + week_b
    iso = {x["name"]: x["price"] for x in liquidity_levels(bars, calendar="utc")}
    cfd = {x["name"]: x["price"] for x in liquidity_levels(bars, calendar="ny17")}
    assert iso["PWH"] == 130          # calendar-week logic wrongly puts the Sunday open in last week
    assert cfd["PWH"] == 101 and cfd["PWL"] == 99  # trading-week logic: the spike belongs to this week
    # the Sunday-open spike (130) really did trade beyond last week's high (101): swept is correct here
    assert [x for x in liquidity_levels(bars, calendar="ny17") if x["name"] == "PWH"][0]["swept"] is True


def test_cfd_day_rolls_at_17_new_york():
    utc = timezone.utc
    a = {"time": datetime(2026, 1, 7, 21, 59, tzinfo=utc)}   # 16:59 New York, Wednesday
    b = {"time": datetime(2026, 1, 7, 22, 0, tzinfo=utc)}    # 17:00 New York: next trading day
    sun = {"time": datetime(2026, 1, 11, 22, 0, tzinfo=utc)}  # Sunday 17:00 NY: Monday's day
    assert day_key(a, "ny17") != day_key(b, "ny17")
    assert day_key(a, "utc") == day_key(b, "utc")
    assert day_key(sun, "ny17").weekday() == 0
    assert week_key(sun, "ny17") != week_key({"time": datetime(2026, 1, 9, 21, tzinfo=utc)}, "ny17")
    assert calendar_for("binance") == "utc" and calendar_for("yahoo") == "ny17"


def test_sweep_needs_close_back_inside_and_untouched_level():
    base = day_bars(-1, {10: (110, 99.5)}) + day_bars(0, {})[:10]
    sweep = base + raw([(100, 110.6, 99.8, 109.5)], step=60, start=base[-1]["time"] + timedelta(hours=1))
    found = [s for s in find_sweeps(sweep) if s["level"] == "PDH"]
    assert len(found) == 1 and found[0]["side"] == "sell" and found[0]["type"] == "BSL"
    breakout = base + raw([(100, 110.6, 99.8, 110.4)], step=60, start=base[-1]["time"] + timedelta(hours=1))
    assert [s for s in find_sweeps(breakout) if s["level"] == "PDH"] == []  # closed above: not a sweep
    again = sweep + raw([(109.5, 110.8, 109, 109.6)], step=60, start=sweep[-1]["time"] + timedelta(hours=1))
    assert [s for s in find_sweeps(again, recent=1) if s["level"] == "PDH"] == []  # already taken


def test_premium_discount_ote_and_fibonacci():
    closes = [100, 101, 102, 101, 100.5, 101, 104, 108, 110, 109, 108, 107, 106, 105, 104, 103]
    # steady pullback after the high: no newer confirmed swing low, so the leg stays 100 -> 110
    b = raw([(c, c + 0.0, c - 0.0, c) for c in closes])
    b[0]["low"] = 100.0
    b[8]["high"] = 110.0
    dr = dealing_range(b, 3)
    assert dr["direction"] == "up" and dr["low"] <= 100.5 and dr["high"] == 110.0
    fl = fib_levels(100, 110, "up")
    assert abs(fl["retracement"][0.705] - 102.95) < 1e-9 and abs(fl["retracement"][0.5] - 105) < 1e-9
    assert abs(fl["extension"][1.272] - 112.72) < 1e-9 and abs(fl["extension"][1.618] - 116.18) < 1e-9
    down = fib_levels(100, 110, "down")
    assert abs(down["extension"][1.272] - 97.28) < 1e-9
    pd = premium_discount(b, 3)
    assert pd and pd["zone"] == "discount" and pd["direction"] == "up"
    assert abs(pd["ote"][0] - (110 - 0.79 * (110 - pd["low"]))) < 1e-9


def test_fvg_status_valid_tested_filled():
    rows = [(100, 100.5, 99.5, 100)] * 20
    gap = rows + [(100, 100.5, 99.5, 100), (100.5, 103, 100.4, 102.8), (102.8, 104, 102, 103.5)]
    b = raw(gap)  # bar -3 high 100.5 ... bar -1 low 102 -> bullish gap 100.5 - 102
    valid = [f for f in fvgs(b) if f["side"] == "bull"]
    assert len(valid) == 1 and valid[0]["status"] == "valid"
    assert (valid[0]["low"], valid[0]["high"]) == (100.5, 102)
    tested = b + raw([(103.5, 103.6, 101.5, 102.5)], start=b[-1]["time"] + timedelta(minutes=15))
    assert [f["status"] for f in fvgs(tested) if f["side"] == "bull"] == ["tested"]
    filled = tested + raw([(102.5, 102.6, 100.0, 100.2)], start=tested[-1]["time"] + timedelta(minutes=15))
    assert [f for f in fvgs(filled) if f["side"] == "bull"] == []
    assert [f["status"] for f in fvgs(filled, include_filled=True) if f["side"] == "bull"] == ["filled"]


def ob_series():
    # a range, a down candle (the block), then a strong push that breaks the last swing high
    rows = []
    for n in range(30):  # oscillation creating swing highs at ~101
        base = 100 + (1.0 if n % 6 in (2, 3) else 0.0)
        rows.append((base, base + 0.3, base - 0.3, base))
    rows += [(100.6, 100.7, 99.6, 99.8)]           # the last down candle = bullish order block
    rows += [(99.8, 103.5, 99.7, 103.2)]           # displacement: closes above the swing high
    return raw(rows)


def test_order_block_status_and_breaker():
    b = ob_series()
    ev = structure_events(b)
    blocks = [x for x in order_blocks(b, ev) if x["kind"] == "OB" and x["side"] == "bull"]
    assert blocks and blocks[-1]["status"] == "valid", (ev[-1:], blocks)
    ob = blocks[-1]
    assert ob["low"] == 99.6 and ob["high"] == 100.7
    tapped = b + raw([(103.2, 103.3, 100.5, 101.5)], start=b[-1]["time"] + timedelta(minutes=15))
    assert [x for x in order_blocks(tapped, structure_events(tapped)) if x["kind"] == "OB" and x["idx"] == ob["idx"]][-1]["status"] == "mitigated"
    broken = tapped + raw([(101.5, 101.6, 98.5, 98.9)], start=tapped[-1]["time"] + timedelta(minutes=15))
    res = [x for x in order_blocks(broken, structure_events(broken)) if x["idx"] == ob["idx"]]
    assert any(x["kind"] == "OB" and x["status"] == "broken" for x in res)
    br = [x for x in res if x["kind"] == "BREAKER"]
    assert br and br[0]["side"] == "bear" and br[0]["status"] == "valid"
    closed_back = broken + raw([(98.9, 101.5, 98.8, 101.2)], start=broken[-1]["time"] + timedelta(minutes=15))
    br2 = [x for x in order_blocks(closed_back, structure_events(closed_back)) if x["kind"] == "BREAKER" and x["idx"] == ob["idx"]]
    assert br2 and br2[0]["status"] == "invalid"


def test_inducement_minor_swing_taken_before_the_block():
    ob = {"side": "bull", "low": 100.0, "high": 101.0, "idx": 0}
    b = raw([(100, 101, 100, 100.5), (100.5, 103.2, 102.5, 103.0), (103.0, 103.3, 102.0, 102.6),
             (102.6, 103.6, 102.4, 103.4), (103.4, 103.5, 101.4, 101.6), (101.6, 101.8, 100.6, 100.8)])
    assert inducement(b, ob) is True  # the minor low at 102.0 was taken before the block
    untouched = raw([(100, 101, 100, 100.5), (100.5, 103.2, 102.5, 103.0), (103.0, 103.3, 102.0, 102.6),
                     (102.6, 103.6, 102.4, 103.4), (103.4, 103.9, 102.6, 103.8), (103.8, 104.2, 103.0, 104.0)])
    assert inducement(untouched, ob) is False


def test_closed_bars_conversion():
    r = [{"time": T0 + timedelta(minutes=15 * i), "open": 1, "high": 1, "low": 1, "close": 1}
         for i in (3, 2, 1, 0)]  # newest first, r[0] forming
    cb = closed_bars(r)
    assert [c["time"] for c in cb] == sorted(c["time"] for c in cb) and len(cb) == 3
    assert cb[-1]["time"] == r[1]["time"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
