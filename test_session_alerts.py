from datetime import datetime, timedelta, timezone

import pytest

import session_alerts as sa

UTC = timezone.utc
DAY = datetime(2026, 11, 17, 7, 0, tzinfo=UTC)           # 02:00 New York


def bar(t, o, h, l, c, v=100):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def m5_pre():
    out = []
    for i in range(96):                                  # 02:00 - 09:55 New York, a steady decline (no fractals, one session range)
        p = 104.0 - 0.05 * i
        out.append(bar(DAY + timedelta(minutes=5 * i), p, p + 0.1, p - 0.1, p - 0.05))
    return out


def at(i):                                               # 10:00 New York + i bars
    return DAY + timedelta(minutes=5 * (96 + i))


def scalper_bars():
    b = m5_pre()
    b.append(bar(at(0), 99.2, 99.6, 98.8, 99.5))         # sweep of the session low, closes back above
    b.append(bar(at(1), 99.5, 99.62, 99.4, 99.55))
    b.append(bar(at(2), 99.7, 101.0, 99.65, 100.9))      # BOS bar leaving an FVG (99.6 - 99.65)
    return b


@pytest.fixture(autouse=True)
def clean():
    sa._STATE.clear()


def test_scalper_signal_in_the_silver_bullet_window():
    ev = sa.scalper("EURUSD", scalper_bars())
    assert [e["event"] for e in ev] == ["SIGNAL"]
    e = ev[0]
    assert e["direction"] == "long" and e["name"] == "ICT Session Scalper"
    assert e["entry"] == pytest.approx((99.6 + 99.65) / 2) and e["sl"] < 98.8 and e["tp1"] > e["entry"] and e["rr"] >= 2
    assert set(("name", "direction", "entry", "sl", "tp1", "tp2", "rr")) <= set(e)
    assert sa.scalper("EURUSD", scalper_bars()) == []                        # one signal per direction per window


def test_scalper_ignores_outside_the_window():
    shift = timedelta(hours=2)
    b = [dict(x, time=x["time"] + shift) for x in scalper_bars()]            # 12:00 New York
    assert sa.scalper("EURUSD", b) == []


def test_scalper_invalidated_when_the_fvg_is_closed_through_and_missed_at_the_window_end():
    b = scalper_bars()
    sa.scalper("EURUSD", b)
    b2 = b + [bar(at(3), 99.7, 99.8, 99.3, 99.4)]                             # closes under the FVG low 99.6
    ev = sa.scalper("EURUSD", b2)
    assert [e["event"] for e in ev] == ["INVALIDATED"]
    sa._STATE.clear()
    sa.scalper("EURUSD", b)
    late = b + [bar(at(12), 101.0, 101.2, 100.9, 101.1)]                       # 11:00 New York, never touched
    assert [e["event"] for e in sa.scalper("EURUSD", late)] == ["MISSED"]


def test_scalper_rejects_a_poor_rr():
    b = scalper_bars()
    for x in b[:96]:
        x["high"] = min(x["high"], 100.6)                                      # the session high is now close: R:R too small
    ev = sa.scalper("EURUSD", b)
    assert ev and ev[0]["event"] == "REJECTED" and "below 1:2" in ev[0]["reason"]


def m15_flat():
    out, t0 = [], datetime(2026, 11, 17, 0, 0, tzinfo=UTC)
    for i in range(60):
        low = 99.0 if i == 30 else 99.8
        out.append(bar(t0 + timedelta(minutes=15 * i), 100.0, 100.2, low, 100.0))
    return out


def smc_bars():
    b = m15_flat()
    t = b[-1]["time"]
    b.append(bar(t + timedelta(minutes=15), 99.8, 99.9, 98.6, 99.4))            # bearish sweep bar of the 99.0 swing low (the OB)
    b.append(bar(t + timedelta(minutes=30), 99.5, 100.6, 99.45, 100.5))         # closes above the structure high
    b.append(bar(t + timedelta(minutes=45), 100.2, 100.3, 99.8, 100.0))         # back into the OB top
    return b


def test_smc_prime_confluence_and_h1_trend():
    ev = sa.smc_prime("EURUSD", smc_bars(), [], "up")
    assert len(ev) == 1 and ev[0]["name"] == "SMC Prime" and ev[0]["direction"] == "long"
    e = ev[0]
    assert e["confluence"] == 3 and e["entry"] == pytest.approx(99.9) and e["sl"] < 98.6
    assert e["tp1"] == pytest.approx(e["entry"] + 2 * (e["entry"] - e["sl"])) and e["tp2"] > e["tp1"]
    assert "sweep" in e["reason"] and "close beyond" in e["invalidation"]
    assert sa.smc_prime("EURUSD", smc_bars(), [], "up") == []                  # not twice
    sa._STATE.clear()
    assert sa.smc_prime("EURUSD", smc_bars(), [], "down") == []                # against the H1 trend
    assert sa.smc_prime("EURUSD", smc_bars(), [], None) == []


def test_smc_prime_needs_two_factors():
    b = m15_flat()                                                             # no sweep, no break
    assert sa.smc_prime("EURUSD", b, [], "up") == []


def pulse_bars(vol=200, body=True):
    out, t0 = [], datetime(2026, 11, 17, 14, 0, tzinfo=UTC)
    for i in range(30):
        out.append(bar(t0 + timedelta(minutes=5 * i), 100.0, 100.2, 99.8, 100.0))
    t = out[-1]["time"] + timedelta(minutes=5)
    out.append(bar(t, 100.0, 101.1, 100.05, 101.0 if body else 100.2, vol))
    return out


def test_pulse_needs_volume_and_body():
    ev = sa.pulse("EURUSD", pulse_bars())
    assert len(ev) == 1 and ev[0]["direction"] == "long" and "ESTIMATE" not in ev[0]["name"] and "estimate" in ev[0]["name"]
    e = ev[0]
    assert e["sl"] < 100.05 and e["tp1"] > e["entry"] and e["rr"] >= 1.0 and "x the 20-bar average" in e["reason"]
    sa._STATE.clear()
    assert sa.pulse("EURUSD", pulse_bars(vol=150)) == []                       # below 1.8x
    assert sa.pulse("EURUSD", pulse_bars(body=False)) == []                    # weak body
    assert sa.pulse("EURUSD", [dict(b, volume=0) for b in pulse_bars()]) == []  # no volume data: no estimate


def test_pulse_invalidated_by_the_next_candle_and_cooldown():
    b = pulse_bars()
    sa.pulse("EURUSD", b)
    nxt = bar(b[-1]["time"] + timedelta(minutes=5), 101.0, 101.0, 99.9, 99.95)
    ev = sa.pulse("EURUSD", b + [nxt])
    assert [e["event"] for e in ev] == ["INVALIDATED"]
    sa._STATE.clear()
    sa.pulse("EURUSD", b)
    again = b + [bar(b[-1]["time"] + timedelta(minutes=5), 101.0, 102.3, 100.95, 102.2, 400)]
    assert sa.pulse("EURUSD", again) == []                                     # cooldown: no repeat for 6 bars


def test_pulse_confirms_the_other_alerts():
    assert sa.pulse_recent("long", pulse_bars()) is True
    assert sa.pulse_recent("short", pulse_bars()) is False


def test_process_is_idempotent_and_renders():
    out = sa.process("EURUSD", scalper_bars(), [], [], None, 5)
    assert [c for c, _, _ in out] == ["SCALP_SIGNAL"]
    t = out[0][2]
    assert "ICT SESSION SCALPER" in t and "Hard SL" in t and "TP2" in t and "R:R to TP1" in t and "Signal only" in t
    assert sa.process("EURUSD", scalper_bars(), [], [], None, 5) == []         # same bar: nothing again


def test_switches(monkeypatch):
    monkeypatch.setenv("SCALPER", "0")
    assert sa.process("EURUSD", scalper_bars(), [], [], None, 5) == []
    monkeypatch.setenv("SESSION_ALERTS", "0")
    assert not sa.enabled("X")


def test_classes_exist():
    import alert_classes
    for c in ("SCALP_SIGNAL", "SCALP_INVALIDATED", "SMC_SIGNAL", "PULSE_SIGNAL", "PULSE_INVALIDATED"):
        assert c in alert_classes.EXTRA_CLASSES and c in alert_classes.AR
