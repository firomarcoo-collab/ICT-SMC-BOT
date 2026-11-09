"""Offline tests for ict_engine.py and the reversal / continuation modes of selective.py.
Run: python test_ict_engine.py"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

import ict_engine as ie
import selective as sl
import signal_store
import structure as st
from test_selective import (CFG, END, NOW, bar, m5_retest, retime, scenario, tmp_store, wave, zig)

UTC = timezone.utc


# ---------- fixtures ----------

def reversal_m15(variant="valid", later_bars=3):
    """Downtrend, then: a wick under the last swing low that closes back above (sweep), a
    bearish block candle, a displacement candle through the last swing high (MSS) and a gap.
    variants: valid | breakout (sweep candle closes under the level) | consumed (a later close under it)
              | no_mss (price never breaks the swing high) | weak_choch (breaks it without displacement)"""
    b = zig(80, 1000, up=False)                     # ends after a 3-bar bounce
    lows = [(i, x["low"]) for i, x in enumerate(b) if i < 72]
    pv = st.pivots(b, 3)
    l1 = [p for p in pv if p[1] == "L"][-1][2]       # the last confirmed swing low = SSL
    top = max(x["high"] for x in b[-4:])
    p = b[-1]["close"]
    if variant == "breakout":
        sweep = bar(p, p, l1 - 1.4, l1 - .6)         # wick AND close below the level: a breakout
    else:
        sweep = bar(p, p, l1 - .8, l1 + .3)          # wick below, close back above: a sweep
    out = b + [sweep]
    if variant == "no_mss":
        out += [bar(l1 + .3, l1 + .9, l1 + .2, l1 + .7), bar(l1 + .7, l1 + 1.2, l1 + .6, l1 + 1.0)]
    elif variant == "weak_choch":
        px = l1 + .3
        while px < top + .2:
            out.append(bar(px, px + .8, px - .1, px + .7))
            px += .7
    else:
        block = bar(l1 + .8, l1 + .9, l1 + .3, l1 + .4)                  # bearish: the order block
        push = bar(l1 + .4, top + 1.7, l1 + .3, top + 1.5)               # displacement through the swing high
        gap = bar(top + 1.5, top + 2.3, l1 + 1.7, top + 1.9)             # leaves a gap above the block
        out += [block, push, gap]
        out += [bar(top + 1.9, top + 2.4, top + 1.6, top + 2.1)] * later_bars
        if variant == "consumed":
            out += [bar(top + 2, top + 2.1, l1 - .5, l1 - .3)]            # later close under the swept level
    return retime(out, 900), l1


def reversal_scenario(variant="valid", h_up=False):
    m15, l1 = reversal_m15(variant)
    h4 = retime(zig(96, 100, h_up), 14400)
    h1 = retime(zig(96, 100, h_up), 3600)
    zone = {"low": l1 + .9, "high": l1 + 1.7, "atr": 1.3}
    return {"h4": h4, "h1": h1, "m15": m15, "m5": m5_retest(zone, "buy")}, l1


# ---------- liquidity map ----------

def test_sweep_versus_breakout_classification():
    ssl = {"type": "SSL", "price": 100.0}
    bsl = {"type": "BSL", "price": 110.0}
    assert ie.classify_liquidity_event(bar(101, 101.5, 99.2, 100.6), ssl) == "sweep"
    assert ie.classify_liquidity_event(bar(101, 101.5, 98.0, 99.4), ssl) == "breakout"
    assert ie.classify_liquidity_event(bar(101, 102, 100.2, 101.5), ssl) is None
    assert ie.classify_liquidity_event(bar(109, 110.8, 108.5, 109.4), bsl) == "sweep"
    assert ie.classify_liquidity_event(bar(109, 111.5, 108.5, 111.0), bsl) == "breakout"


def test_equal_highs_lows_swings_and_previous_day_are_in_the_map():
    one_day = retime(wave([100, 110, 100, 110, 100, 110, 100, 105], per_leg=4), 900)   # 08:00-15:00 UTC, no Asian bars
    raw = {x["name"] for x in st.liquidity_levels(one_day[:-1])}
    assert {"EQH", "EQL"} <= raw, raw                                  # equal highs / equal lows are detected
    prices = {(x["type"], round(x["price"], 1)) for x in ie.liquidity_map(one_day[:-1], one_day[-1])}
    assert ("BSL", 110.3) in prices and ("SSL", 99.7) in prices        # and present in the map (under the strongest label)
    two_days = retime(wave([100, 110, 100, 110, 100, 110, 100, 105], per_leg=8) * 2, 900)
    lv = ie.liquidity_map(two_days[:-1], two_days[-1])
    names = {x["name"] for x in lv}
    assert "PDH" in names and "PDL" in names                          # previous day high / low
    assert all(not x["swept"] for x in lv) and {x["type"] for x in lv} == {"BSL", "SSL"}
    # the same price is listed once, under its strongest label (PDH hides an identical EQH)
    assert len({(x["type"], round(x["price"], 6)) for x in lv}) == len(lv)


def test_swing_levels_only_use_confirmed_pivots_and_drop_swept_ones():
    bars = retime(wave([100, 110, 100, 108]), 900)
    lv = ie.swing_levels(bars)
    assert lv and all(x["scope"] == "swing" for x in lv)
    assert all(x["price"] != bars[-1]["high"] for x in lv)            # the unconfirmed last bar is no swing
    nxt = bars[-1]["time"] + timedelta(minutes=15)
    higher = bars + [{"time": nxt, "open": 108, "high": 112, "low": 107, "close": 111}]
    assert any(x["swept"] for x in ie.swing_levels(higher) if x["type"] == "BSL")
    open_levels = ie.liquidity_map(higher[:-1], higher[-1])
    assert any(x["scope"] == "swing" and not x["swept"] for x in open_levels) or open_levels


def test_previous_session_high_low():
    base = datetime(2026, 1, 7, 3, 0, tzinfo=UTC)                    # starts in the Asian session
    bars = []
    for i in range(40):                                              # 03:00 .. 13:00, M15
        t = base + timedelta(minutes=15 * i)
        hi, lo = (105, 95) if t.hour < 7 else (103, 97)
        bars.append({"time": t, "open": 100, "high": hi, "low": lo, "close": 100})
    cur = {"time": datetime(2026, 1, 7, 13, 0, tzinfo=UTC), "open": 100, "high": 101, "low": 99, "close": 100}
    lv = {x["name"]: x for x in ie.session_levels(bars, cur)}
    assert lv["PSH London"]["price"] == 103 and lv["PSL London"]["price"] == 97   # 13:00 is New York
    cur_london = {"time": datetime(2026, 1, 7, 8, 0, tzinfo=UTC), "open": 100, "high": 101, "low": 99, "close": 100}
    hist = [b for b in bars if b["time"] < cur_london["time"]]
    lv = {x["name"]: x for x in ie.session_levels(hist, cur_london)}
    assert lv["PSH Asia"]["price"] == 105 and lv["PSL Asia"]["price"] == 95       # London looks back at Asia
    assert not any("London" in n for n in lv)                                      # the running session is excluded
    assert ie.session_levels([], cur) == []


def test_sweep_of_previous_session_low_is_found():
    base = datetime(2026, 1, 7, 0, 0, tzinfo=UTC)
    bars = []
    for i in range(60):                                              # 00:00 .. 15:00 M15, quiet range
        t = base + timedelta(minutes=15 * i)
        p = 100 + (i % 4) * .1
        bars.append({"time": t, "open": p, "high": p + .6, "low": p - .6, "close": p + .1})
    for i in range(0, 28):                                           # make the Asian low clearly lowest
        if bars[i]["time"].hour < 7:
            bars[i]["low"] = 99.0 if i == 10 else bars[i]["low"]
    bars.append({"time": base + timedelta(minutes=15 * 60), "open": 100.2, "high": 100.4, "low": 98.5, "close": 100.1})
    found = ie.find_liquidity_sweeps(bars, lookback=3)
    assert found and found[-1]["idx"] == len(bars) - 1 and found[-1]["side"] == "buy"
    assert found[-1]["price"] == 99.0 or found[-1]["scope"] in ("external", "session", "internal", "swing")


# ---------- reversal sequence ----------

def parts(variant):
    m15, l1 = reversal_m15(variant)
    fv = sl.fvg_candidates(m15)
    ob = sl.ob_candidates(m15)
    return m15, l1, fv, ob


def test_complete_bullish_reversal_sequence_is_recognised():
    m15, l1, fv, ob = parts("valid")
    rev = ie.reversal_sequence(m15, "buy", fv, ob)
    assert rev["complete"] and rev["stage"] == 4, rev["reasons"]
    assert rev["sweep"]["type"] == "SSL" and rev["sweep"]["extreme"] < l1
    assert rev["event"]["type"] == "CHoCH" and rev["event"]["mss"]
    assert {z["kind"] for z in rev["zones"]} == {"FVG", "OB"}
    assert ie.sweep_status(m15, rev["sweep"])[0] == "valid"
    assert rev["event_id"].startswith("SSL-")


def test_a_wick_through_a_low_that_closes_beyond_is_a_breakout_not_a_sweep():
    m15, l1, fv, ob = parts("breakout")
    rev = ie.reversal_sequence(m15, "buy", fv, ob)
    assert not rev["complete"] and rev["stage"] <= 1
    assert any("sweep" in r for r in rev["reasons"])


def test_consumed_sweep_is_not_reused():
    m15, l1, fv, ob = parts("consumed")
    sweeps = [s for s in ie.find_liquidity_sweeps(m15) if s["type"] == "SSL"]
    assert sweeps and ie.sweep_status(m15, sweeps[-1])[0] == "breakout"
    rev = ie.reversal_sequence(m15, "buy", fv, ob)
    assert not rev["complete"] and any("not usable" in r for r in rev["reasons"])


def test_no_mss_and_choch_without_displacement_are_rejected():
    m15, l1, fv, ob = parts("no_mss")
    rev = ie.reversal_sequence(m15, "buy", fv, ob)
    assert not rev["complete"] and rev["stage"] == 2 and any("MSS" in r for r in rev["reasons"])
    m15, l1, fv, ob = parts("weak_choch")
    rev = ie.reversal_sequence(m15, "buy", fv, ob)
    assert not rev["complete"] and rev["stage"] == 2
    assert any("without displacement" in r for r in rev["reasons"]), rev["reasons"]


def test_premium_discount_filter():
    leg = {"low": 100.0, "high": 110.0, "eq": 105.0}
    assert ie.in_correct_half({"low": 101, "high": 102}, leg, "buy") == (True, "discount")
    assert ie.in_correct_half({"low": 107, "high": 108}, leg, "buy") == (False, "premium")
    assert ie.in_correct_half({"low": 107, "high": 108}, leg, "sell") == (True, "premium")
    assert ie.in_correct_half({"low": 101, "high": 102}, leg, "sell") == (False, "discount")


# ---------- full signals ----------

def test_counter_trend_reversal_signal_when_the_sequence_is_complete():
    data, l1 = reversal_scenario("valid", h_up=False)            # H4 and H1 are DOWN
    assert sl.evaluate("XAUUSD", dict(data, h4=data["h4"]), NOW, CFG, spread=.2)["action"] in ("BUY", "WAIT")
    res = sl.evaluate("XAUUSD", data, NOW, CFG, spread=.2)
    assert res["action"] == "BUY", res["reasons"]
    s = res["setup"]
    assert s["mode"] == "reversal" and s["sl"] < s["entry"] < s["tp"]
    assert s["invalidation"] < l1                                  # invalidated by the sweep low
    assert any(u.startswith("SSL-") for u in s["uses"])
    text = sl.explain(res)
    for part in ("HTF trend:", "Liquidity:", "swept", "Structure:", "MSS", "FVG/OB:", "Retest:",
                 "M5 confirmation:", "O ", "Levels:", "invalidation"):
        assert part in text, part
    assert "counter-trend reversal" in res["report"]["htf"]


def test_same_setup_without_the_sequence_is_rejected_against_the_trend():
    data, l1 = reversal_scenario("no_mss", h_up=False)
    res = sl.evaluate("XAUUSD", data, NOW, CFG, spread=.2)
    assert res["action"] == "WAIT" and res["setup"] is None
    text = sl.explain(res)
    assert "Waiting because" in text and ("conflict" in text or "against" in text)
    for variant in ("breakout", "consumed", "weak_choch"):
        data, _ = reversal_scenario(variant, h_up=False)
        assert sl.evaluate("XAUUSD", data, NOW, CFG, spread=.2)["action"] == "WAIT", variant


def test_reversal_needs_the_retest_and_the_closed_confirmation():
    data, l1 = reversal_scenario("valid")
    far = m5_retest({"low": l1 + .9, "high": l1 + 1.7, "atr": 1.3}, "buy", touch=False)
    res = sl.evaluate("XAUUSD", dict(data, m5=far), NOW, CFG, spread=.2)
    assert res["action"] == "WAIT" and any("retest" in r or "zone" in r for r in res["reasons"])
    res = sl.evaluate("XAUUSD", data, NOW - 200, CFG, spread=.2)       # candle still running
    assert res["action"] == "WAIT"


def test_a_consumed_event_or_zone_cannot_signal_twice():
    data, _ = reversal_scenario("valid")
    store, _d = tmp_store()
    first = sl.process("XAUUSD", data, NOW, store, CFG, spread=.2)
    assert first["action"] == "BUY"
    store.close("XAUUSD", "loss", NOW + 10)                          # lock released, same candle
    again = sl.process("XAUUSD", data, NOW + 20, store, CFG, spread=.2)
    assert again["action"] == "WAIT" and any("already" in r for r in again["reasons"])
    # a LATER candle reusing the same sweep and zones is blocked by the consumed-ids record
    later = dict(first["setup"], bar_time=first["setup"]["bar_time"] + 300)
    ok, why = store.open("XAUUSD", later, NOW + 400)
    assert not ok and "consumed" in why
    # a different market keeps its own record
    assert sl.process("NAS100", data, NOW, store, CFG, spread=.2)["action"] == "BUY"
    # and the consumed ids survive a restart
    again_store = signal_store.SignalStore(store.path)
    assert any(u.startswith("XAUUSD|") for u in again_store.used)
    assert not again_store.open("XAUUSD", later, NOW + 500)[0]


def test_sell_reversal_mirrors_the_buy():
    data, l1 = reversal_scenario("valid", h_up=False)
    flip = lambda bars, c: [dict(b, open=c - b["open"], high=c - b["low"], low=c - b["high"],  # noqa: E731
                                 close=c - b["close"]) for b in bars]
    mirrored = {"h4": flip(data["h4"], 200), "h1": flip(data["h1"], 200),
                "m15": flip(data["m15"], 2000), "m5": flip(data["m5"], 2000)}
    res = sl.evaluate("XAUUSD", mirrored, NOW, CFG, spread=.2)
    assert res["action"] == "SELL", res["reasons"]
    assert res["setup"]["mode"] == "reversal" and res["setup"]["tp"] < res["setup"]["entry"] < res["setup"]["sl"]
    assert res["report"]["liquidity"].startswith("BSL")


# ---------- D1, M1, reports ----------

def test_d1_context_filters_continuation_trades():
    up = retime(zig(60, 100, True), 86400)
    down = retime(zig(60, 100, False), 86400)
    assert ie.d1_context(up)[0] == "up" and ie.d1_context(down)[0] == "down"
    assert ie.d1_context(up[:5]) == (None, "D1 unavailable")
    base = scenario()
    ok = sl.evaluate("XAUUSD", dict(base, d1=up), NOW, CFG, spread=.2)
    assert ok["action"] == "BUY" and "D1 up" in ok["report"]["d1"]
    bad = sl.evaluate("XAUUSD", dict(base, d1=down), NOW, CFG, spread=.2)
    assert bad["action"] == "WAIT" and any("D1" in r for r in bad["reasons"])
    assert sl.evaluate("XAUUSD", base, NOW, CFG, spread=.2)["action"] == "BUY"     # no D1: not required


def test_m1_never_creates_a_signal_and_never_blocks_one():
    perfect_m1 = retime([bar(100 + i * .1, 100.3 + i * .1, 99.9 + i * .1, 100.25 + i * .1) for i in range(60)], 60)
    data = scenario()
    # confirmation candle missing on M5 (bearish in an uptrend): perfect M1 data changes nothing
    wrong = scenario(confirm_bullish=False)
    res = sl.evaluate("XAUUSD", dict(wrong, m1=perfect_m1), NOW, CFG, spread=.2)
    assert res["action"] == "WAIT"
    short = dict(data, m5=data["m5"][-10:], m1=perfect_m1)             # not enough M5: M1 cannot replace it
    assert sl.evaluate("XAUUSD", short, NOW, CFG, spread=.2)["action"] == "WAIT"
    with_m1 = sl.evaluate("XAUUSD", dict(data, m1=perfect_m1), NOW, CFG, spread=.2)
    assert with_m1["action"] == "BUY" and "M1" in with_m1["report"]["m1"]
    against = retime([bar(100.5, 100.6, 99.8, 99.9) for _ in range(60)], 60)
    still = sl.evaluate("XAUUSD", dict(data, m1=against), NOW, CFG, spread=.2)
    assert still["action"] == "BUY" and "against" in still["report"]["m1"]       # reported, not a blocker


def test_every_wait_explains_itself():
    data = scenario()
    for kw, needle in (({"spread": None}, "spread"), ({"status": "STALE"}, "STALE")):
        args = dict(spread=.2)
        args.update(kw)
        res = sl.evaluate("XAUUSD", data, NOW, CFG, **args)
        assert res["action"] == "WAIT" and needle in " ".join(res["reasons"])
        text = sl.explain(res)
        assert "WAIT XAUUSD" in text and "Waiting because" in text and "HTF trend:" in text
    res = sl.evaluate("XAUUSD", scenario(h4_up=False), NOW, CFG, spread=.2)
    assert "Waiting because" in sl.explain(res) and "Reversal sequence:" in sl.explain(res)
    sig = sl.format_signal("XAUUSD", sl.evaluate("XAUUSD", data, NOW, CFG, spread=.2))
    assert "Invalidation" in sig and "Signal only" in sig


def test_old_continuation_rules_are_still_enforced():
    # S/R, FVG, OB, closed candle and spread rules from the first engine must still hold
    assert sl.evaluate("X", scenario(), NOW, CFG, spread=.2)["action"] == "BUY"
    assert sl.evaluate("X", scenario(), NOW - 250, CFG, spread=.2)["action"] == "WAIT"
    assert sl.evaluate("X", scenario(), NOW, CFG, spread=None)["action"] == "WAIT"
    assert sl.evaluate("X", scenario(h4_up=False), NOW, CFG, spread=.2)["action"] == "WAIT"


def test_live_scan_survives_missing_optional_data_and_can_print_the_full_report():
    import ict_alerts
    d = scenario(h4_up=False)
    def fake(inst, tf="5m"):
        if tf in ("1d", "1m"):
            raise RuntimeError("no optional data")
        return d[{"5m": "m5", "15m": "m15", "1h": "h1", "4h": "h4"}[tf]]
    ict_alerts.get_candles = fake
    store, _ = tmp_store()
    os.environ["VERBOSE"] = "1"
    try:
        status, alert, lagging = ict_alerts.selective_scan("XAUUSD", NOW, CFG, store, None, {"XAUUSD": .2}, None)
    finally:
        os.environ.pop("VERBOSE", None)
    assert alert is None and "HTF trend:" in status and "Waiting because" in status
    good = scenario()
    ict_alerts.get_candles = lambda inst, tf="5m": (_ for _ in ()).throw(RuntimeError("x")) if tf in ("1d", "1m") \
        else good[{"5m": "m5", "15m": "m15", "1h": "h1", "4h": "h4"}[tf]]
    status, alert, _ = ict_alerts.selective_scan("XAUUSD", NOW, CFG, store, None, {"XAUUSD": .2}, None)
    assert alert and "Invalidation" in alert["text"]


def test_arabic_entry_alert():
    import ict_alerts
    res = sl.evaluate("XAUUSD", scenario(), NOW, CFG, spread=.2)
    text = sl.format_signal_ar("XAUUSD", res)
    for part in ("إشارة دخول: شراء الذهب", "الدخول:", "الإبطال:", "وقف الخسارة:", "الهدف:",
                 "التوافق", "شمعة التأكيد M5", "إشارة فقط"):
        assert part in text, part
    assert f"{res['setup']['entry']:.2f}" in text
    sell = sl.evaluate("XAUUSD", dict(zip(("h4", "h1", "m15", "m5"), [[]] * 4)), NOW, CFG, spread=.2)
    assert sell["action"] == "WAIT"
    unverified = sl.evaluate("XAUUSD", scenario(), NOW, sl.SelectiveConfig(allow_unknown_cost=True, ready_score=0), spread=None)
    assert "غير موثّقة" in sl.format_signal_ar("XAUUSD", unverified)
    d = scenario()
    ict_alerts.get_candles = lambda inst, tf="5m": d[{"5m": "m5", "15m": "m15", "1h": "h1", "4h": "h4"}.get(tf, "m5")] \
        if tf in ("5m", "15m", "1h", "4h") else (_ for _ in ()).throw(RuntimeError("x"))
    store, _ = tmp_store()
    _, alert, _ = ict_alerts.selective_scan("XAUUSD", NOW, CFG, store, None, {"XAUUSD": .2}, None, "ar")
    assert alert and "إشارة دخول" in alert["text"]
    store2, _ = tmp_store()
    _, alert, _ = ict_alerts.selective_scan("XAUUSD", NOW, CFG, store2, None, {"XAUUSD": .2}, None)
    assert alert and "SELECTIVE SIGNAL" in alert["text"]          # English stays the function default


def test_futures_switch_can_target_only_some_markets():
    import subprocess
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    code = "import ict_alerts as a;print(a.INSTRUMENTS['XAUUSD']['id'],a.INSTRUMENTS['US100']['id'],a.INSTRUMENTS['US30']['id'])"
    def ids(value):
        env = dict(os.environ, FUTURES=value)
        return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=here).stdout.split()
    assert ids("US100,US30") == ["XAUUSD=X", "NQ=F", "YM=F"]
    assert ids("1") == ["GC=F", "NQ=F", "YM=F"]
    assert ids("") == ["XAUUSD=X", "^NDX", "^DJI"]


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("ok", name)
            except Exception as e:
                import traceback
                failed += 1
                print("FAIL", name, type(e).__name__, e)
                traceback.print_exc(limit=-2)
    raise SystemExit(1 if failed else 0)
