"""Offline checks for asia_breakout.py: gold Asia-range breakout, retest, confirmation, structural stop, filters."""
from datetime import datetime, timedelta, timezone

import alert_classes as ac
import asia_breakout as ab
import structure as st

START = datetime(2026, 11, 18, 16, 0, tzinfo=timezone.utc)            # 11:00 New York (EST, UTC-5)
ASIA = [(2000, 2001, 1999, 2000)] * 32                                 # 00:00-08:00 UTC = 19:00-03:00 ET
PRE = [(2000, 2000.5, 1999.5, 2000)] * 32                              # 16:00-24:00 UTC
QUIET = (2000.0, 2000.5, 1999.8, 2000.2)                               # 08:00 UTC = 03:00 ET
BREAK = (2000.5, 2003.5, 2000.3, 2003.2)                               # 08:15 UTC, strong body above 2001
RETEST = (2003.2, 2003.4, 2001.1, 2001.5)                              # comes back to the edge
CONFIRM = (2001.6, 2004.2, 2001.4, 2004.0)                             # bullish close back above the edge


def mirror(rows):
    return [(4000 - o, 4000 - l, 4000 - h, 4000 - c) for o, h, l, c in rows]


def bars(rows, vol=100, vols=None):
    out = []
    for n, (o, h, l, c) in enumerate(rows):
        out.append({"time": START + timedelta(minutes=15 * n), "open": o, "high": h, "low": l, "close": c,
                    "volume": (vols or {}).get(n, vol)})
    return out


def h1_bars(n=40, rng=1.2):
    return [{"time": START - timedelta(hours=40 - k), "open": 2000, "high": 2000 + rng / 2, "low": 2000 - rng / 2, "close": 2000, "volume": 1}
            for k in range(n)]


def now_of(b):
    return int(b[-1]["time"].timestamp()) + 900


def run(state, rows, h1_trend="up", vols=None, gate=None, side="buy"):
    rows = rows if side == "buy" else mirror(rows)
    b = bars(rows, vols=vols)
    h1 = h1_bars()
    if side == "sell":
        h1 = [dict(x) for x in h1]
    return ab.process(state, "XAUUSD", b, h1, h1_trend, now_of(b), "en", 2, "utc", gate)


def new_state(tmp_path, name="a.json"):
    return ac.AlertState(str(tmp_path / name))


BASE = PRE + ASIA + [QUIET]
BREAK_IDX = len(BASE)
HIGH_VOL = {BREAK_IDX: 300}


def test_full_buy_flow_setup_retest_confirmation_with_a_structural_stop(tmp_path):
    s = new_state(tmp_path)
    got = run(s, BASE + [BREAK], vols=HIGH_VOL)
    assert [c for c, _, _ in got] == ["ASIA_SETUP"]
    assert "SETUP ONLY" in got[0][2] and "Confluence" in got[0][2] and "DXY filter not used" in got[0][2]
    assert run(s, BASE + [BREAK], vols=HIGH_VOL) == []                                         # once
    assert run(s, BASE + [BREAK, RETEST], vols=HIGH_VOL) == []                                  # retest alone is not a signal
    got = run(s, BASE + [BREAK, RETEST, CONFIRM], vols=HIGH_VOL, gate=lambda plan: (True, ""))
    assert [c for c, _, _ in got] == ["ASIA_SIGNAL"]
    t = got[0][2]
    assert "CONFIRMED ENTRY" in t and "Hard SL" in t and "not at the range edge" in t and "Early Exit" in t and "break-even" in t and "R:R: 1:2.0" in t
    sid = got[0][1]
    plan = s.setups[sid]["plan"]
    rec = s.setups[sid]
    assert plan["side"] == "buy" and plan["entry"] == 2004.0 and plan["profile"] == "asia" and plan["market_entry"] is True
    assert plan["sl"] == 2001.1 - 0.5 * rec["atr"] < 2001.0                                     # behind the retest low, below the edge itself
    risk = plan["entry"] - plan["sl"]
    assert abs(plan["tp"] - (plan["entry"] + 2 * risk)) < 1e-9 and abs(plan["tp1"] - (plan["entry"] + risk)) < 1e-9
    assert s.meta["pending"][sid]["early_exit"] == "2,001.00" and s.meta["pending"][sid]["limit"] is False
    assert run(s, BASE + [BREAK, RETEST, CONFIRM], vols=HIGH_VOL, gate=lambda p: (True, "")) == []


def test_the_sell_side_mirrors_the_buy_side(tmp_path):
    s = new_state(tmp_path)
    run(s, BASE + [BREAK], vols=HIGH_VOL, h1_trend="down", side="sell")
    got = run(s, BASE + [BREAK, RETEST, CONFIRM], vols=HIGH_VOL, h1_trend="down", side="sell", gate=lambda p: (True, ""))
    assert [c for c, _, _ in got] == ["ASIA_SIGNAL"]
    plan = s.setups[got[0][1]]["plan"]
    assert plan["side"] == "sell" and plan["sl"] > 1999.0 + 0.4                                 # above the retest high plus 0.5 ATR


def test_gate_failure_gives_a_confirmed_alert_that_is_not_a_signal(tmp_path):
    s = new_state(tmp_path)
    run(s, BASE + [BREAK], vols=HIGH_VOL)
    got = run(s, BASE + [BREAK, RETEST, CONFIRM], vols=HIGH_VOL, gate=lambda p: (False, "cost too high"))
    assert [c for c, _, _ in got] == ["ASIA_CONFIRMED", "DROP"] and "NOT issued as a signal: cost too high" in got[0][2]


def test_filters_reject_weak_breaks_wrong_trend_and_low_volume(tmp_path):
    wick = (2000.4, 2003.5, 2000.3, 2001.4)                                                     # closes above the edge but a small body
    got = run(new_state(tmp_path), BASE + [wick], vols=HIGH_VOL)
    assert [c for c, _, _ in got] == ["DROP"] and "body" in got[0][2]
    got = run(new_state(tmp_path, "b.json"), BASE + [BREAK], vols=HIGH_VOL, h1_trend="down")
    assert [c for c, _, _ in got] == ["DROP"] and "H1 trend" in got[0][2]
    got = run(new_state(tmp_path, "c.json"), BASE + [BREAK])                                    # volume equal to the average
    assert [c for c, _, _ in got] == ["DROP"] and "volume" in got[0][2]


def test_a_huge_or_tiny_range_is_skipped(tmp_path):
    wide = [(2000, 2010, 1990, 2000)] * 32                                                      # 20 wide vs H1 ATR 1.2
    b = bars(PRE + wide + [QUIET, (2010.5, 2014.0, 2010.3, 2013.7)], vols=HIGH_VOL)
    got = ab.process(new_state(tmp_path), "XAUUSD", b, h1_bars(), "up", now_of(b), "en", 2, "utc")
    assert [c for c, _, _ in got] == ["DROP"] and "H1 ATR" in got[0][2]


def test_two_closes_back_inside_is_a_fakeout_and_invalidates(tmp_path):
    s = new_state(tmp_path)
    run(s, BASE + [BREAK], vols=HIGH_VOL)
    back = [(2002, 2002.2, 2000.2, 2000.6), (2000.6, 2000.9, 2000.0, 2000.4)]
    got = run(s, BASE + [BREAK] + back, vols=HIGH_VOL)
    assert [c for c, _, _ in got] == ["INVALIDATED"] and "SETUP INVALIDATED" in got[0][2] and "fake breakout" in got[0][2]


def test_no_chasing_without_a_retest(tmp_path):
    s = new_state(tmp_path)
    run(s, BASE + [BREAK], vols=HIGH_VOL)
    run_away = (2003.2, 2010.0, 2003.0, 2009.5)
    got = run(s, BASE + [BREAK, run_away], vols=HIGH_VOL)
    assert [c for c, _, _ in got] == ["DROP"] and "do not chase" in got[0][2]


def test_other_markets_and_the_off_switch_do_nothing(tmp_path, monkeypatch):
    b = bars(BASE + [BREAK], vols=HIGH_VOL)
    assert ab.process(new_state(tmp_path), "BTCUSD", b, h1_bars(), "up", now_of(b), "en", 2, "utc") == []
    monkeypatch.setenv("ASIA_BREAKOUT", "0")
    assert ab.process(new_state(tmp_path, "z.json"), "XAUUSD", b, h1_bars(), "up", now_of(b), "en", 2, "utc") == []


def test_track_setups_wires_the_asia_signal_into_the_signal_list(tmp_path, monkeypatch):
    import ict_alerts as ia
    import selective
    monkeypatch.setattr(ia, "alert_state", lambda: s)
    monkeypatch.setattr(ia, "h1_trend", lambda d, n: "up")
    s = new_state(tmp_path, "w.json")
    rows = BASE + [BREAK]
    b = bars(rows, vols=HIGH_VOL)
    data = {"m15": b, "m5": [], "h1": h1_bars(), "h4": [], "m1": [], "d1": []}
    cfg = selective.make_config("scalp") if hasattr(selective, "make_config") else None
    sigs = []
    ia.track_setups("XAUUSD", "XAUUSD", data, now_of(b), cfg, "utc", "en", None, None, 2, lambda p: (True, ""), sigs)
    assert any(r.get("kind") == "ASIA" for r in s.setups.values())
