import session_alerts as sa


def bars(up=True):
    """An M15 zigzag with clear swings (k=3): a swing low 100, a swing high 110, then a pullback. up=False mirrors it."""
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
    closes = [105, 104, 103, 102, 101, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 109, 108, 107, 106, 105]
    if not up:
        closes = [210 - c for c in closes]
    return [{"time": t0 + timedelta(minutes=15 * i), "open": c, "high": c + 0.2, "low": c - 0.2, "close": c, "volume": 1}
            for i, c in enumerate(closes)]


def ev(direction="long", entry=100.0, name="SMC Prime"):
    return {"name": name, "event": "SIGNAL", "symbol": "XAUUSD", "direction": direction, "entry": entry, "sl": entry - 5, "tp1": entry + 5,
            "tp2": entry + 10, "rr": 2.0, "bar_time": "2026-11-25T03:00:00", "reason": "x"}


def test_signal_inside_the_golden_zone_passes_and_shows_the_fib_line(monkeypatch):
    monkeypatch.setenv("FIB_GATE", "1")
    import selective
    monkeypatch.setattr(selective, "fib_check", lambda m15, side, entry, lo, hi: (True, "Fib: entry at 62% of the M15 leg, INSIDE the 50-79% golden zone"))
    out = sa.fib_gate(ev(), [])
    assert out["event"] == "SIGNAL" and "INSIDE" in out["fib"]
    assert "📐 Fib: entry at 62%" in sa.render(out)


def test_signal_outside_the_zone_becomes_wait(monkeypatch):
    monkeypatch.setenv("FIB_GATE", "1")
    import selective
    monkeypatch.setattr(selective, "fib_check", lambda *a: (False, "Fib: entry at 20% of the M15 leg, outside the 50-79% golden zone"))
    out = sa.fib_gate(ev(), [])
    assert out["event"] == "REJECTED" and "outside" in out["reason"]
    assert sa.CLASS.get((out["name"], out["event"]), "SESSION_INFO") == "SESSION_INFO"        # no alert, no tracking


def test_no_clear_leg_is_not_confirmed(monkeypatch):
    monkeypatch.setenv("FIB_GATE", "1")
    out = sa.fib_gate(ev(), [])                                                              # empty candle list: no leg
    assert out["event"] == "REJECTED" and "Fibonacci confirmation is required" in out["reason"]


def test_real_leg_and_bounds_with_the_real_function(monkeypatch):
    monkeypatch.setenv("FIB_GATE", "1")
    import selective
    up = bars(True)
    pd = selective.st.premium_discount(up, 3)
    assert pd and pd["direction"] == "up"                                                    # the leg really is detected
    rng = pd["high"] - pd["low"]
    mid, shallow = pd["high"] - 0.6 * rng, pd["high"] - 0.2 * rng
    assert sa.fib_gate(ev(entry=mid), up)["event"] == "SIGNAL"                               # 60% retracement: inside 50-79%
    assert sa.fib_gate(ev(entry=shallow), up)["event"] == "REJECTED"                         # 20%: too shallow
    assert sa.fib_gate(ev(entry=pd["high"] - 0.9 * rng), up)["event"] == "REJECTED"          # 90%: too deep
    assert sa.fib_gate(ev("short", entry=mid), up)["event"] == "REJECTED"                    # a sell needs a down leg
    down = bars(False)
    pd2 = selective.st.premium_discount(down, 3)
    assert pd2 and pd2["direction"] == "down"
    entry = pd2["low"] + 0.6 * (pd2["high"] - pd2["low"])
    assert sa.fib_gate(ev("short", entry=entry), down)["event"] == "SIGNAL"


def test_other_events_are_untouched_and_the_gate_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("FIB_GATE", "1")
    inv = dict(ev(), event="INVALIDATED")
    assert sa.fib_gate(inv, []) is inv
    monkeypatch.setenv("FIB_GATE", "0")
    assert sa.fib_gate(ev(), [])["event"] == "SIGNAL"
