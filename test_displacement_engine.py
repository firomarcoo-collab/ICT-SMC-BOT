import json
from datetime import datetime, timedelta, timezone

import pytest

import displacement_engine as de

UTC = timezone.utc
TARGET = datetime(2026, 11, 17, 13, 0, tzinfo=UTC)         # 08:00 New York = inside the NY killzone


def bar(t, o, h, l, c, v=100):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def htf_bars(trend_up=True, level=112.0, until=TARGET):
    out, t, i = [], until - timedelta(hours=80), 0
    while t < until:
        base = 100 + 0.14 * i if trend_up else 120 - 0.14 * i
        out.append(bar(t, base, base + .3, base - .3, base))
        t += timedelta(hours=1)
        i += 1
    prev = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)         # a bar of the previous New York day carries the level
    for b in out:
        if b["time"] == prev:
            b.update(high=level) if level >= b["high"] else b.update(low=level)
    return out


def flat(n, end=TARGET, price=111.3, v=100):
    t0 = end - timedelta(minutes=15 * n)
    return [bar(t0 + timedelta(minutes=15 * i), price, price + .2, price - .3, price + .0, v) for i in range(n)]


def prelude():
    b = flat(38)
    b.append(bar(TARGET - timedelta(minutes=30), 111.3, 111.5, 111.0, 111.3))          # t-2: high 111.5
    b.append(bar(TARGET - timedelta(minutes=15), 111.3, 111.6, 111.2, 111.5))          # t-1: close 111.5
    return b


def long_expansion(vol=200):
    return prelude() + [bar(TARGET, 111.6, 113.1, 111.55, 112.9, vol)]


def feed(eng, ltf, htf, extra):
    ev = eng.on_bar(ltf, htf)
    for e in extra:
        ltf = ltf + [e]
        ev += eng.on_bar(ltf, htf)
    return ev, ltf


def at(i):
    return TARGET + timedelta(minutes=15 * i)


RETEST = lambda i: bar(at(i), 112.8, 112.9, 112.0, 112.5)              # noqa: E731
CONFIRM = lambda i: bar(at(i), 112.1, 112.9, 112.05, 112.8)             # noqa: E731


def names(ev):
    return [e["event"] for e in ev]


def test_full_lifecycle_long_expansion():
    eng, htf = de.Engine("XAUUSD"), htf_bars()
    ev, _ = feed(eng, long_expansion(), htf, [RETEST(1), CONFIRM(2)])
    assert names(ev) == ["SETUP", "RETEST", "CONFIRMED"] and eng.state == de.COOLDOWN
    setup, conf = ev[0], ev[2]
    assert setup["mode"] == "expansion" and setup["direction"] == "long" and setup["invalidation"] == 112.0
    atr = de.atr_at(long_expansion()[:-1], 14)
    assert conf["entry"] == 112.8 and conf["sl"] < 112.0                 # retest extreme minus 0.5 ATR
    r = conf["entry"] - conf["sl"]
    assert conf["tp1"] == pytest.approx(conf["entry"] + r) and conf["tp3"] == pytest.approx(conf["entry"] + 3 * r)
    assert conf["risk_scale"] == 1.0 and "break-even" in conf["actions"]["tp1"]
    assert json.loads(eng.to_json(conf))["event"] == "CONFIRMED"
    assert atr is not None


def test_idempotent_same_bar_never_emits_twice():
    eng, htf, ltf = de.Engine("X"), htf_bars(), long_expansion()
    assert names(eng.on_bar(ltf, htf)) == ["SETUP"]
    assert eng.on_bar(ltf, htf) == []
    assert eng.on_bar(ltf[:-1], htf) == []                              # an older bar is ignored too


def test_no_setup_outside_the_killzone():
    late = datetime(2026, 11, 17, 18, 0, tzinfo=UTC)                    # 13:00 New York
    shift = late - TARGET
    ltf = [dict(b, time=b["time"] + shift) for b in long_expansion()]
    htf = [dict(b, time=b["time"]) for b in htf_bars()]
    assert de.Engine("X").on_bar(ltf, htf) == []


def test_volume_filter_and_missing_volume():
    htf = htf_bars()
    assert de.Engine("X").on_bar(long_expansion(vol=120), htf) == []    # below 1.3 x average
    nov = [dict(b, volume=0) for b in long_expansion()]
    assert de.Engine("X").on_bar(nov, htf) == []                         # volume_required
    assert names(de.Engine("X", de.Config(volume_required=False)).on_bar(nov, htf)) == ["SETUP"]


def test_expansion_needs_the_htf_bias():
    ev = de.Engine("X").on_bar(long_expansion(), htf_bars(trend_up=False))
    assert all(e.get("mode") != "expansion" for e in ev)


def test_missed_by_timeout_and_by_chase():
    eng, htf = de.Engine("X"), htf_bars()
    drift = [bar(at(i), 112.95, 113.1, 112.9, 113.0) for i in range(1, 3)]
    ev, _ = feed(eng, long_expansion(), htf, drift)                      # 113.0 - 112.9 < 1.2 ATR, still waiting
    assert names(ev) == ["SETUP"] and eng.state == de.IMPULSE
    eng2 = de.Engine("X")
    run = [bar(at(1), 113.0, 115.0, 113.0, 114.9)]
    ev2, _ = feed(eng2, long_expansion(), htf, run)
    assert names(ev2) == ["SETUP", "MISSED"] and "anti-chase" in ev2[-1]["reason"] and eng2.state == de.IDLE
    eng3 = de.Engine("X", de.Config(max_wait_bars=1))
    ev3, _ = feed(eng3, long_expansion(), htf, [bar(at(1), 112.95, 113.1, 112.9, 113.0), bar(at(2), 112.95, 113.1, 112.9, 113.0)])
    assert names(ev3) == ["SETUP", "MISSED"] and "no retest" in ev3[-1]["reason"]


def test_invalidated_by_two_closes_beyond_the_level():
    eng, htf = de.Engine("X"), htf_bars()
    ev, _ = feed(eng, long_expansion(), htf, [bar(at(1), 112.4, 112.5, 111.5, 111.8), bar(at(2), 111.8, 111.9, 111.4, 111.6)])
    assert names(ev) == ["SETUP", "RETEST", "INVALIDATED"] and eng.state == de.IDLE


def test_retest_close_beyond_invalidates_immediately():
    eng, htf = de.Engine("X"), htf_bars()
    ev, _ = feed(eng, long_expansion(), htf, [RETEST(1), bar(at(2), 112.4, 112.5, 111.5, 111.8)])
    assert names(ev) == ["SETUP", "RETEST", "INVALIDATED"]


def test_killzone_end_marks_missed():
    eng, htf = de.Engine("X"), htf_bars()
    far = TARGET + timedelta(hours=3)                                    # 11:00 New York: the window is closed
    ev, _ = feed(eng, long_expansion(), htf, [bar(far, 112.9, 113.0, 112.8, 112.9)])
    assert names(ev) == ["SETUP", "MISSED"] and "killzone" in ev[-1]["reason"]


def test_cooldown_blocks_a_second_signal_the_same_day_then_resets():
    eng, htf = de.Engine("X"), htf_bars()
    ev, ltf = feed(eng, long_expansion(), htf, [RETEST(1), CONFIRM(2)])
    assert eng.state == de.COOLDOWN
    ev2 = eng.on_bar(ltf + [bar(at(3), 112.8, 114.5, 112.7, 114.4, 400)], htf)
    assert "SETUP" not in names(ev2)
    nxt = TARGET + timedelta(days=1)
    eng.on_bar(ltf + [bar(nxt, 112.8, 112.9, 112.7, 112.8)], htf)
    assert eng.state == de.IDLE


def test_early_invalidation_after_entry_once():
    eng, htf = de.Engine("X"), htf_bars()
    ev, ltf = feed(eng, long_expansion(), htf, [RETEST(1), CONFIRM(2)])
    a = eng.on_bar(ltf + [bar(at(3), 112.6, 112.7, 111.5, 111.8)], htf)
    assert names(a) == ["INVALIDATED"] and "early exit" in a[0]["reason"]
    b = eng.on_bar(ltf + [bar(at(3), 112.6, 112.7, 111.5, 111.8), bar(at(4), 111.8, 111.9, 111.5, 111.6)], htf)
    assert b == []


def test_sweep_reversal_short_against_the_bias_halves_the_risk():
    htf = htf_bars(trend_up=True, level=112.0)
    base = flat(38, price=111.3)
    base.append(bar(TARGET - timedelta(minutes=30), 111.8, 111.9, 111.7, 111.8))       # t-2: low 111.7
    base.append(bar(TARGET - timedelta(minutes=15), 111.8, 112.3, 111.6, 111.7))       # t-1 wicks above 112.0
    sweep = bar(TARGET, 111.6, 112.3, 110.0, 110.2, 250)                                # bearish displacement, high < t-2 low is false
    # bearish FVG needs high_t < low_{t-2}
    base[-2] = bar(TARGET - timedelta(minutes=30), 112.0, 112.1, 111.9, 112.0)
    sweep = bar(TARGET, 111.8, 111.85, 110.0, 110.2, 250)
    sweep["high"] = 111.85
    base[-1] = bar(TARGET - timedelta(minutes=15), 111.9, 112.3, 111.8, 111.9)           # the wick above the 112.0 pool
    eng = de.Engine("X")
    ev = eng.on_bar(base + [sweep], htf)
    assert names(ev) == ["SETUP"] and ev[0]["mode"] == "sweep" and ev[0]["direction"] == "short"
    assert ev[0]["counter_trend"] is True and ev[0]["invalidation"] == 112.3


def test_config_is_the_single_place_for_thresholds():
    c = de.Config()
    assert (c.body_ratio, c.range_atr, c.vol_mult, c.max_wait_bars, c.sl_atr, c.killzones) == \
        (0.60, 1.2, 1.3, 16, 0.5, ((2.0, 5.0), (8.0, 11.0)))
