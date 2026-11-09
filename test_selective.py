"""Offline tests for selective.py and signal_store.py (synthetic candles, no internet).
Run: python test_selective.py"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

import selective as sl
import signal_store
import structure as st

UTC = timezone.utc
END = datetime(2026, 1, 7, 15, 0, tzinfo=UTC)          # the last candle of every series closes here
NOW = int(END.timestamp()) + 20                          # 20 s after the M5 confirmation closed
CFG = sl.SelectiveConfig()


def bar(o, h, low, c):
    return {"time": None, "open": o, "high": h, "low": low, "close": c}


def retime(bars, sec, end=END):
    n = len(bars)
    return [dict(b, time=end - timedelta(seconds=sec * (n - i))) for i, b in enumerate(bars)]


def zig(n, base=100.0, up=True):
    """Clean trend: 5 bars of 1.0 in the trend direction, 3 bars of 0.8 against it, repeated."""
    out, p, s = [], base, (1 if up else -1)
    while len(out) < n:
        for d in (1, 1, 1, 1, 1, -.8, -.8, -.8):
            o, c = p, p + d * s
            out.append(bar(o, max(o, c) + .2, min(o, c) - .2, c))
            p = c
            if len(out) >= n:
                break
    return out


def wave(levels, per_leg=6):
    """Range between price levels; every swing touches its level (+-0.3 wick)."""
    out, p = [], levels[0]
    for lv in levels[1:]:
        for i in range(1, per_leg + 1):
            c = p + (lv - p) * i / per_leg
            o = out[-1]["close"] if out else p
            out.append(bar(o, max(o, c) + .3, min(o, c) - .3, c))
        p = lv
    return out


# ---------- confirmation and OHLC ----------

def test_bullish_and_bearish_confirmation_from_ohlc():
    prev = bar(100, 101, 99, 100.5)
    assert sl.confirmation([prev, bar(100.5, 102, 100.4, 101.8)], "buy") == (True, True, "")
    ok, strong, _ = sl.confirmation([prev, bar(100.2, 100.9, 100.0, 100.8)], "buy")
    assert ok and not strong            # bullish but did not close above the previous high
    assert sl.confirmation([prev, bar(100.5, 100.6, 99.5, 99.8)], "buy")[0] is False
    assert sl.confirmation([prev, bar(100.0, 100.1, 98.0, 98.5)], "sell") == (True, True, "")
    ok, strong, _ = sl.confirmation([prev, bar(100.0, 100.1, 99.0, 99.2)], "sell")
    assert ok and not strong            # bearish but did not close below the previous low
    assert sl.confirmation([prev, bar(99.5, 101, 99.4, 100.6)], "sell")[0] is False


def test_wick_breakout_is_not_a_close_breakout():
    spike = bar(100, 103, 99.5, 100.4)          # wick through 102, closed back below
    assert sl.classify_break(spike, 102, "up") == "wick"
    assert sl.classify_break(bar(100, 104, 99.5, 103.2), 102, "up") == "close"
    assert sl.classify_break(bar(100, 101, 99, 100), 102, "up") is None
    assert sl.classify_break(bar(100, 100.5, 96, 99.6), 98, "down") == "wick"
    assert sl.classify_break(bar(100, 100.5, 96, 96.5), 98, "down") == "close"


def test_unclosed_candle_is_dropped_and_never_signals():
    sec = 300
    c = retime([bar(1, 2, 0, 1)] * 5, sec)
    now = int(c[-1]["time"].timestamp()) + sec          # exactly when the last one closes
    assert len(sl.closed_only(c, sec, now)) == 5
    assert len(sl.closed_only(c, sec, now - 1)) == 4     # one second earlier it is still forming
    data = scenario()                                    # a complete valid buy scenario
    assert sl.evaluate("T", data, NOW, CFG, spread=.2)["action"] == "BUY"
    forming = dict(data["m5"][-1])
    data2 = dict(data, m5=data["m5"][:-1] + [dict(forming, time=END - timedelta(seconds=100))])
    res = sl.evaluate("T", data2, NOW - 250, CFG, spread=.2)   # confirmation candle still running
    assert res["action"] == "WAIT" and res["setup"] is None


# ---------- support / resistance ----------

def sr_series():
    return retime(wave([100, 110, 100, 110, 100, 110, 100, 108]), 3600)


def zone_at(zones, price):
    return next((z for z in zones if z["low"] - .5 <= price <= z["high"] + .5), None)


def test_support_zone_found_and_strength():
    zones = sl.sr_zones(sr_series())
    sup = zone_at(zones, 99.7)
    assert sup and sup["role"] == "support" and sup["strength"] == "strong" and sup["touches"] >= 3
    res = zone_at(zones, 110.3)
    assert res and res["role"] == "resistance"
    weak = sl.sr_zones(retime(wave([100, 110, 100, 108]), 3600))
    assert all(z["strength"] == "weak" for z in weak)    # only two touches: weak


def test_invalid_support_breakout_flips_role_but_a_wick_does_not():
    base = wave([100, 110, 100, 110, 100, 110, 100, 108])
    wick = base + [bar(108, 108.5, 98.6, 106)]                    # wick under support, closes back
    assert zone_at(sl.sr_zones(retime(wick, 3600)), 99.7)["role"] == "support"
    broken = base + [bar(108, 108.2, 96, 96.5), bar(96.5, 97.5, 95.8, 97)]   # decisive closes below
    z = zone_at(sl.sr_zones(retime(broken, 3600)), 99.7)
    assert z["role"] == "resistance" and z["flips"] == 1


def m5_retest(zone, side="buy", last=None, touch=True):
    """40 quiet M5 bars on the entry side, then a pullback into the zone and a confirmation candle."""
    lo, hi = zone["low"], zone["high"]
    bars = []
    for i in range(70):
        p = (hi + 6 - i * 0.05) if side == "buy" else (lo - 6 + i * 0.05)
        bars.append(bar(p, p + .3, p - .3, p + (.1 if side == "buy" else -.1)))
    if side == "buy":
        a = hi + 2
        bars.append(bar(a + 1, a + 1.2, a - .2, a))                       # coming down
        t = (hi - .1) if touch else (hi + 2)
        bars.append(bar(a, a + .1, t - .3, t))                            # touches the zone
        bars.append(last or bar(t, t + 1.6, t - .1, t + 1.4))             # bullish confirmation
    else:
        a = lo - 2
        bars.append(bar(a - 1, a + .2, a - 1.2, a))
        t = (lo + .1) if touch else (lo - 2)
        bars.append(bar(a, t + .3, a - .1, t))
        bars.append(last or bar(t, t + .1, t - 1.6, t - 1.4))
    return retime(bars, 300)


def test_support_retest_accepted_and_no_retest_rejected():
    zone = {"low": 100.0, "high": 100.6, "atr": 1.3}
    m5 = m5_retest(zone)
    assert sl.retest(m5, zone, "buy", CFG) == (True, "")
    far = m5_retest(zone, touch=False)
    ok, why = sl.retest(far, zone, "buy", CFG)
    assert not ok and "not come back" in why
    sup = {"low": 100.0, "high": 100.6, "atr": 1.3}
    sell = m5_retest({"low": 110.0, "high": 110.6, "atr": 1.3}, "sell")
    assert sl.retest(sell, {"low": 110.0, "high": 110.6, "atr": 1.3}, "sell", CFG)[0]
    assert sup["low"] == 100.0


def test_close_beyond_the_invalidation_boundary_rejects_the_retest():
    zone = {"low": 100.0, "high": 100.6, "atr": 1.3}
    m5 = m5_retest(zone)
    dec = CFG.sr_decisive_atr * zone["atr"]
    # a wick far below the zone that closes back above it is only a sweep: still valid
    wick = m5[:-2] + [dict(m5[-2], low=zone["low"] - 2, close=zone["high"] - .1), m5[-1]]
    assert sl.retest(wick, zone, "buy", CFG)[0]
    # a candle that CLOSES decisively below the zone invalidates it
    broke = m5[:-2] + [dict(m5[-2], low=zone["low"] - 2, close=zone["low"] - dec - .1), m5[-1]]
    ok, why = sl.retest(broke, zone, "buy", CFG)
    assert not ok and "below" in why
    under = m5[:-1] + [dict(m5[-1], close=zone["low"] - .05, open=zone["low"] - .5)]
    assert not sl.retest(under, zone, "buy", CFG)[0]


# ---------- FVG ----------

def with_fvg(disp=True, later=lambda p, lo, hi: ()):
    """Uptrend zigzag, then candle A, an impulse candle M and candle C that leave a gap.
    `later(p, lo, hi)` returns the bars that follow (p = price before the pattern)."""
    b = zig(96, 1000)
    p = b[-1]["close"]
    a = bar(p, p + .5, p - .1, p + .3)
    body = 2.7 if disp else 0.35
    m = bar(p + .3, p + .3 + body + .2, p + .2, p + .3 + body)
    top = m["close"]
    if disp:
        c = bar(top, top + .7, p + 1.5, top + .5)
        lo, hi = p + .5, p + 1.5
    else:
        c = bar(top, top + 1.2, p + 1.0, top + .8)
        lo, hi = p + .5, p + 1.0
    return retime(b + [a, m, c] + list(later(p, lo, hi)), 900), (lo, hi)


def above(p, n=3):
    return [bar(p + 6, p + 7, p + 5, p + 6.5)] * n


def test_valid_fresh_fvg_and_partial_fill():
    bars, (lo, hi) = with_fvg(later=lambda p, lo, hi: above(p))
    f = [x for x in sl.fvg_candidates(bars) if abs(x["low"] - lo) < 1e-6][0]
    assert f["valid"] and f["status"] == "fresh" and f["side"] == "bull"
    bars2, _ = with_fvg(later=lambda p, lo, hi: above(p, 2) + [bar(p + 6, p + 6.2, hi - .3, p + 5.5)])
    f2 = [x for x in sl.fvg_candidates(bars2) if abs(x["low"] - lo) < 1e-6][0]
    assert f2["status"] == "partial" and f2["valid"] and 0 < f2["fill"] <= .5


def test_invalid_fvg_consumed_weak_or_too_deep():
    gone, (lo, hi) = with_fvg(later=lambda p, lo, hi: above(p, 2) + [bar(p + 6, p + 6.2, lo - .5, lo - .2)])
    f = [x for x in sl.fvg_candidates(gone) if abs(x["low"] - lo) < 1e-6][0]
    assert f["status"] == "mitigated" and not f["valid"]
    assert any("consumed" in r for r in f["reasons"])
    deep, _ = with_fvg(later=lambda p, lo, hi: above(p, 2) + [bar(p + 6, p + 6.2, hi - .75, p + 5.5)])
    f = [x for x in sl.fvg_candidates(deep) if abs(x["low"] - lo) < 1e-6][0]
    assert not f["valid"] and any("filled" in r for r in f["reasons"])
    weak, (wlo, _) = with_fvg(disp=False, later=lambda p, lo, hi: above(p, 2))
    cands = [x for x in sl.fvg_candidates(weak) if abs(x["low"] - wlo) < 1e-6]
    assert cands and not cands[0]["valid"]
    assert any("displacement" in r for r in cands[0]["reasons"])


# ---------- Order block ----------

def with_ob(displacement=True, later=lambda p, top, lo, hi: ()):
    """Up zigzag that ends on a pullback, then a down candle (the block) and a push through the swing high."""
    b = zig(96, 1000)                        # ends at the bottom of a pullback
    swing_high = max(x["high"] for x in b[-12:])
    p = b[-1]["close"]
    block = bar(p, p + .3, p - .7, p - .5)    # the last down candle
    top = swing_high + 2.5 if displacement else swing_high - 1.0   # else: no break of structure
    push = bar(p - .5, top + .2, p - .6, top)
    lo, hi = p - .7, p + .3
    return retime(b + [block, push] + list(later(p, top, lo, hi)), 900), (lo, hi)


def test_valid_order_block():
    bars, (lo, hi) = with_ob(later=lambda p, top, lo, hi: [bar(top + 3, top + 4, top + 2, top + 3.5)] * 3)
    obs = [o for o in sl.ob_candidates(bars) if abs(o["low"] - lo) < 1e-6]
    assert obs and obs[0]["valid"] and obs[0]["event"] in ("BOS", "CHoCH")


def test_invalid_order_blocks_mitigated_broken_or_not_displaced():
    def away(top):
        return [bar(top + 3, top + 4, top + 2, top + 3.5)] * 2
    bars, (lo, hi) = with_ob(later=lambda p, top, lo, hi: away(top) + [bar(top + 3, top + 3.2, hi - .1, top + 2)])
    o = [x for x in sl.ob_candidates(bars) if abs(x["low"] - lo) < 1e-6][0]
    assert not o["valid"] and any("mitigated" in r for r in o["reasons"])
    bars, _ = with_ob(later=lambda p, top, lo, hi: away(top) + [bar(top + 3, top + 3.2, lo - 3, lo - 2)])
    o = [x for x in sl.ob_candidates(bars) if abs(x["low"] - lo) < 1e-6][0]
    assert not o["valid"] and any("through" in r for r in o["reasons"])
    bars, _ = with_ob(displacement=False)       # a push that is not a displacement creates no block
    assert [x for x in sl.ob_candidates(bars) if abs(x["low"] - lo) < 1e-6] == []
    plain = retime(zig(96, 1000), 900)           # ordinary opposite candles inside a trend are not blocks
    assert all(not (o["valid"] and o["idx"] > 90) for o in sl.ob_candidates(plain))


# ---------- higher-timeframe trend, costs ----------

def scenario(h4_up=True, h1_up=True, m15_up=True, confirm_bullish=True):
    """A full buy setup: uptrend on H4/H1/M15, a fresh bullish M15 FVG and OB, an M5 retest + confirmation."""
    h4 = retime(zig(96, 100, h4_up), 14400)
    h1 = retime(zig(96, 100, h1_up), 3600)
    m15, (lo, hi) = with_fvg(later=lambda p, lo, hi: above(p))
    if not m15_up:
        m15 = retime(zig(96, 1000, False), 900)
        lo, hi = 995, 996
    zone = {"low": lo, "high": hi, "atr": 1.3}
    m5 = m5_retest(zone, "buy" if confirm_bullish else "sell")
    return {"h4": h4, "h1": h1, "m15": m15, "m5": m5}


def test_full_buy_setup_gives_a_signal_with_levels():
    res = sl.evaluate("XAUUSD", scenario(), NOW, CFG, spread=.2)
    assert res["action"] == "BUY", res["reasons"]
    s = res["setup"]
    assert s["sl"] < s["entry"] < s["tp"] and abs((s["tp"] - s["entry"]) / (s["entry"] - s["sl"]) - 2) < 1e-9
    assert res["score"] >= CFG.min_confluence and res["checks"]["retest"] and res["checks"]["confirm"]
    text = sl.format_signal("XAUUSD", res)
    assert "Signal only" in text and "LIVE" in text and "not a promised win rate" in text


def test_sell_setup_mirrors_the_buy():
    # mirror every price around 2000: highs become lows, buy becomes sell
    def flip(bars):
        return [dict(b, open=2000 - b["open"], high=2000 - b["low"], low=2000 - b["high"],
                     close=2000 - b["close"]) for b in bars]
    d = scenario()
    # H4/H1 mirrored around 200, M15/M5 around 2000
    mirrored = {"h4": [dict(b, open=200 - b["open"], high=200 - b["low"], low=200 - b["high"],
                            close=200 - b["close"]) for b in d["h4"]],
                "h1": [dict(b, open=200 - b["open"], high=200 - b["low"], low=200 - b["high"],
                            close=200 - b["close"]) for b in d["h1"]],
                "m15": flip(d["m15"]), "m5": flip(d["m5"])}
    res = sl.evaluate("XAUUSD", mirrored, NOW, CFG, spread=.2)
    assert res["action"] == "SELL", res["reasons"]
    assert res["setup"]["tp"] < res["setup"]["entry"] < res["setup"]["sl"]


def test_higher_timeframe_conflict_returns_wait():
    for kw in ({"h4_up": False}, {"h1_up": False}, {"h4_up": False, "h1_up": False}):
        res = sl.evaluate("XAUUSD", scenario(**kw), NOW, CFG, spread=.2)
        assert res["action"] == "WAIT" and res["setup"] is None, kw
        assert any("higher-timeframe" in r or "H4 is" in r or "reversal" in r for r in res["reasons"]), res["reasons"]
    res = sl.evaluate("XAUUSD", scenario(m15_up=False), NOW, CFG, spread=.2)
    assert res["action"] == "WAIT" and any("M15" in r for r in res["reasons"])


def test_confirmation_direction_must_match_the_trend():
    res = sl.evaluate("XAUUSD", scenario(confirm_bullish=False), NOW, CFG, spread=.2)
    assert res["action"] == "WAIT"        # a bearish confirmation inside an uptrend is not a sell


def h1_with_mss():
    """H1 downtrend, then a displacement candle closing above the last swing high (CHoCH + MSS)."""
    b = zig(64, 140, False)
    hi = max(x["high"] for x in b[-10:])
    p = b[-1]["close"]
    b += [bar(p, p + .2, p - .6, p - .4), bar(p - .4, p + .1, p - 1.0, p - .8)]
    q = b[-1]["close"]
    b.append(bar(q, hi + 4.2, q - .2, hi + 4))
    r = b[-1]["close"]
    b += [bar(r, r + 1.2, r - .1, r + 1.0), bar(r + 1, r + 2.2, r + .9, r + 2.0), bar(r + 2, r + 2.3, r + 1.0, r + 1.2)]
    return retime(b, 3600)


def test_reversal_is_allowed_only_with_a_recent_h1_mss():
    h1 = h1_with_mss()
    ev = st.structure_events(h1, 3)
    assert st.current_trend(ev) == "up" and ev[-1]["mss"]
    down4 = retime(zig(96, 140, False), 14400)
    m15 = scenario()["m15"]
    ok, label = sl.htf_alignment(down4, h1, m15, "buy", CFG)
    assert ok and "reversal" in label
    assert not sl.htf_alignment(down4, h1, m15, "buy", sl.SelectiveConfig(reversal_max_age=0))[0]
    plain_up_h1 = retime(zig(96, 100, True), 3600)          # H1 up without an MSS: H4 down still wins
    ok, why = sl.htf_alignment(down4, plain_up_h1, m15, "buy", CFG)
    assert not ok and "not confirmed" in why
    # and the same reversal does not allow a SELL against the new H1 trend
    assert not sl.htf_alignment(down4, h1, m15, "sell", CFG)[0]


def test_spread_and_execution_filters():
    d = scenario()
    assert sl.evaluate("X", d, NOW, CFG, spread=None)["action"] == "WAIT"
    res = sl.evaluate("X", d, NOW, CFG, spread=None)
    assert any("spread unknown" in r for r in res["reasons"])
    wide = sl.evaluate("X", d, NOW, CFG, spread=1.5)           # far too large next to a ~2.4 stop
    assert wide["action"] == "WAIT" and any("of the stop" in r for r in wide["reasons"])
    assert sl.evaluate("X", d, NOW, CFG, spread=.2)["action"] == "BUY"
    pricey = sl.evaluate("X", d, NOW, CFG, spread=.2, commission_price=.5)
    assert pricey["action"] == "WAIT"                           # commission pushes cost over the limit
    allow = sl.SelectiveConfig(allow_unknown_cost=True)
    res = sl.evaluate("X", d, NOW, allow, spread=None)
    assert res["action"] == "BUY" and res["setup"]["cost_note"] == "COST UNVERIFIED"
    ok, cost, note = sl.cost_check(2.0, .2, 0, sl.SelectiveConfig(min_net_rr=1.95))
    assert not ok and "net R:R" in note


def test_old_confirmation_candle_is_not_signalled():
    res = sl.evaluate("X", scenario(), NOW + 600, CFG, spread=.2)
    assert res["action"] == "WAIT" and any("too old" in r for r in res["reasons"])


def test_data_status_is_honest():
    c = retime([bar(1, 2, 0, 1)] * 5, 300)
    now = int(c[-1]["time"].timestamp()) + 300
    assert sl.data_status("binance", c, 300, now) == "LIVE"
    assert sl.data_status("yahoo", c, 300, now + 7200) == "STALE"
    assert sl.data_status("test", c, 300, now) == "SIMULATED"
    assert sl.data_status("binance", [], 300, now) == "DISCONNECTED"
    assert sl.data_status("binance", c, 300, now, connected=False) == "DISCONNECTED"
    assert sl.data_status("binance", c, 300, now, historical=True) == "HISTORICAL"
    d = scenario()
    for status in ("SIMULATED", "STALE", "DISCONNECTED", "HISTORICAL"):
        res = sl.evaluate("X", d, NOW, CFG, spread=.2, status=status)
        assert res["action"] == "WAIT" and status in res["reasons"][0]
    hist = sl.SelectiveConfig(allow_historical=True)
    assert sl.evaluate("X", d, NOW, hist, spread=.2, status="HISTORICAL")["action"] == "BUY"


def test_not_enough_data_returns_wait():
    d = scenario()
    short = dict(d, m5=d["m5"][-20:])
    res = sl.evaluate("X", short, NOW, CFG, spread=.2)
    assert res["action"] == "WAIT" and "not enough" in res["reasons"][0]


# ---------- locks, duplicates, persistence ----------

def tmp_store(**kw):
    d = tempfile.mkdtemp()
    return signal_store.SignalStore(os.path.join(d, "state.json"), **kw), d


def setup_for(side="buy", bar_time=1000):
    return {"side": side, "entry": 100.0, "sl": 99.0, "tp": 102.0, "bar_time": bar_time}


def test_duplicate_and_conflicting_signals_are_blocked():
    store, _ = tmp_store()
    assert store.open("XAUUSD", setup_for("buy", 1000), 5000) == (True, "")
    ok, why = store.open("XAUUSD", setup_for("buy", 1300), 5100)
    assert not ok and "duplicate" in why                       # same side, new candle
    ok, why = store.open("XAUUSD", setup_for("sell", 1300), 5100)
    assert not ok and "conflicting" in why                     # opposite side while one is active
    store.close("XAUUSD", "loss", 5200)
    ok, why = store.open("XAUUSD", setup_for("buy", 1000), 5300)
    assert not ok and "already issued" in why                  # the same candle never signals twice
    assert store.open("XAUUSD", setup_for("sell", 1600), 5300)[0]


def test_markets_have_independent_locks():
    store, _ = tmp_store()
    assert store.open("XAUUSD", setup_for("buy"), 5000)[0]
    assert store.open("BTCUSD", setup_for("sell"), 5000)[0]    # a different market is not blocked
    assert store.open("NAS100", setup_for("buy"), 5000)[0]
    assert not store.open("XAUUSD", setup_for("sell", 2000), 5000)[0]
    store.close("XAUUSD", "win", 5100)
    assert store.active("XAUUSD") is None and store.active("BTCUSD")["side"] == "sell"


def test_signal_state_survives_a_restart():
    store, d = tmp_store()
    store.open("US30", setup_for("buy", 1000), 5000)
    again = signal_store.SignalStore(os.path.join(d, "state.json"))
    assert again.active("US30")["id"] == "US30-buy-1000"
    assert not again.open("US30", setup_for("sell", 2000), 5100)[0]
    assert not again.open("US30", setup_for("buy", 1000), 5100)[0]
    assert os.path.exists(os.path.join(d, "state.jsonl"))      # the audit log is written


def test_corrupt_state_file_is_kept_aside_and_does_not_crash():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "state.json")
    open(path, "w").write("{not json")
    store = signal_store.SignalStore(path)
    assert store.load_error and os.path.exists(path + ".bad") and store.active("XAUUSD") is None


def test_old_active_signal_expires():
    store, _ = tmp_store(ttl=3600)
    store.open("XAUUSD", setup_for("buy", 1000), 5000)
    assert not store.open("XAUUSD", setup_for("buy", 1300), 5000 + 3000)[0]
    assert store.open("XAUUSD", setup_for("sell", 1600), 5000 + 3700)[0]      # expired, lock released


def test_process_takes_the_lock_only_when_a_signal_is_issued():
    store, _ = tmp_store()
    d = scenario()
    wait = sl.process("XAUUSD", d, NOW, store, CFG, spread=None)               # cost unknown: WAIT
    assert wait["action"] == "WAIT" and store.active("XAUUSD") is None
    first = sl.process("XAUUSD", d, NOW, store, CFG, spread=.2)
    assert first["action"] == "BUY" and store.active("XAUUSD")["id"] == first["id"]
    second = sl.process("XAUUSD", d, NOW + 5, store, CFG, spread=.2)           # same market, same candle
    assert second["action"] == "WAIT" and any("blocked" in r for r in second["reasons"])
    other = sl.process("BTCUSD", d, NOW, store, CFG, spread=.2)                # another market is free
    assert other["action"] == "BUY"


def test_tracker_result_releases_the_lock():
    import tracker
    store, d = tmp_store()
    store.open("XAUUSD", setup_for("buy", 1000), 5000)
    trk = tracker.Tracker(os.path.join(d, "r.csv"))
    trk.on_done = lambda it, outcome: store.close(it["ref"], outcome, 6000)
    trk.add("XAUUSD", "5m", "selective", {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 102.0}, 1000,
            ref="XAUUSD")
    bars = [{"time": datetime.fromtimestamp(1300 + 300 * i, tz=UTC), "open": 100, "high": 100.5,
             "low": 99.8, "close": 100.2} for i in range(2)]
    bars.append({"time": datetime.fromtimestamp(2000, tz=UTC), "open": 100, "high": 103, "low": 99.9, "close": 102.5})
    forming = dict(bars[-1], time=datetime.fromtimestamp(2400, tz=UTC))
    notes = trk.update("XAUUSD", "5m", [forming] + list(reversed(bars)))
    assert any("WIN" in n for n in notes) and store.active("XAUUSD") is None


def test_live_scan_end_to_end_with_locks_and_status():
    import ict_alerts
    d = scenario()
    ict_alerts.INSTRUMENTS["SIMTEST"] = {"source": "test", "id": "X", "digits": 2}
    ict_alerts.get_candles = lambda inst, tf="5m": d[{"5m": "m5", "15m": "m15", "1h": "h1", "4h": "h4"}[tf]]
    store, _ = tmp_store()
    spreads = {"XAUUSD": .2}
    status, alert, lagging = ict_alerts.selective_scan("XAUUSD", NOW, CFG, store, None, spreads, None)
    assert alert and "SELECTIVE BUY" in status and "data LIVE]" in alert["text"] and not lagging
    assert store.active("XAUUSD") and alert["market"] == "XAUUSD"
    status, alert2, _ = ict_alerts.selective_scan("XAUUSD", NOW + 30, CFG, store, None, spreads, None)
    assert alert2 is None and "WAIT" in status                     # active lock: no second signal
    status, alert3, _ = ict_alerts.selective_scan("US100", NOW, CFG, store, None, {"US100": .2}, None)
    assert alert3 and alert3["market"] == "NAS100"                 # a different market is independent
    status, none, _ = ict_alerts.selective_scan("SIMTEST", NOW, CFG, store, None, {}, None)
    assert none is None and "SIMULATED" in status or "covers" in status
    # no spread known anywhere -> WAIT, never a guess
    store2, _ = tmp_store()
    status, none, _ = ict_alerts.selective_scan("XAUUSD", NOW, CFG, store2, None, {}, None)
    assert none is None and "spread unknown" in status
    # stale feed -> WAIT with an honest status
    status, none, _ = ict_alerts.selective_scan("XAUUSD", NOW + 7200, CFG, store2, None, spreads, None)
    assert none is None and "STALE" in status
    # the feed is late by one candle: ask the caller to retry instead of judging old data
    status, none, lagging = ict_alerts.selective_scan("XAUUSD", NOW + 300, CFG, store2, None, spreads, None)
    assert none is None and lagging


def test_legacy_ict_alert_can_be_switched_off():
    import ict_alerts
    from ict_smc_bot import Config
    from test_logic import make_candles
    candles = make_candles()
    ict_alerts.INSTRUMENTS["TEST"] = {"source": "test", "id": "TEST", "digits": 2}
    ict_alerts.get_candles = lambda inst, tf="5m": candles
    now = int(candles[-1]["time"].timestamp()) + 8
    on = ict_alerts.analyze("TEST", Config(), now, set())[1]
    off = ict_alerts.analyze("TEST", Config(), now, set(), None, {"legacy_ict": False})[1]
    assert [a for a in on if a["kind"] == "ict"] and not [a for a in off if a["kind"] == "ict"]


def _future(d, rising):
    last = d["m5"][-1]
    e = last["close"]
    s = 1 if rising else -1
    return [{"time": last["time"] + timedelta(seconds=300 * i), "open": e + s * i * .5,
             "high": e + s * i * .5 + .6, "low": e + s * i * .5 - .2, "close": e + s * i * .5 + .4 * s}
            for i in range(1, 12)]


def test_backtest_charges_costs_and_has_no_lookahead():
    import backtest_selective as bt
    d = scenario()
    win = bt.replay(dict(d, m5=d["m5"] + _future(d, True)), CFG, spread=.2, start=70)
    assert len(win) == 1 and win[0]["result"] == "win" and 1.8 < win[0]["R"] < 2.0   # 2R minus costs
    loss = bt.replay(dict(d, m5=d["m5"] + _future(d, False)), CFG, spread=.2, start=70)
    assert len(loss) == 1 and loss[0]["result"] == "loss" and loss[0]["R"] < -1.0     # -1R minus costs
    # the replay must not use candles after the signal: same result when the future is removed from view
    assert bt.replay(d, CFG, spread=.2, start=70) == []          # trade still open at the end of data: not counted
    s = bt.summarize(win + loss)
    assert s["n"] == 2 and s["small_sample"] and abs(s["win_rate"] - .5) < 1e-9 and s["max_drawdown_R"] > 1
    assert bt.summarize([])["n"] == 0
    parts = bt.split_report(win * 5 + loss * 5)
    assert parts["in_sample"]["n"] == 7 and parts["out_of_sample"]["n"] == 3


def test_tracker_market_entry_starts_open_and_checks_stop_first():
    import tracker
    d = tempfile.mkdtemp()
    trk = tracker.Tracker(os.path.join(d, "r.csv"))
    sig = {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 102.0}
    trk.add("XAUUSD", "5m", "selective", sig, 1000, ref="XAUUSD", market=True)
    assert trk.items[0]["state"] == "open"
    both = {"time": datetime.fromtimestamp(1300, tz=UTC), "open": 100.5, "high": 102.5, "low": 98.5, "close": 101}
    forming = dict(both, time=datetime.fromtimestamp(1600, tz=UTC))
    notes = trk.update("XAUUSD", "5m", [forming, both])
    assert any("LOSS" in n for n in notes)                       # touched both: the stop counts first


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("ok", name)
            except Exception as e:
                failed += 1
                print("FAIL", name, type(e).__name__, e)
    raise SystemExit(1 if failed else 0)
