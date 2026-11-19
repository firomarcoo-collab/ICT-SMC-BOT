"""Offline checks for flip_ifvg.py: state flip, IFVG retest / confirmation, cooldown, no chasing, templates."""
from datetime import timedelta

import alert_classes as ac
import flip_ifvg as fl
from test_structure import raw

FILL = [(100, 100.5, 99.5, 100)] * 36
GAP = FILL + [(100, 100.5, 99.5, 100.2), (100.2, 103, 100.1, 102.8), (102.8, 103.5, 102, 103)]      # bull FVG 100.5-102
BREAK = (103, 103.2, 98.5, 99.0)                                                                     # full-body displacement close below 100.5
HOLD = (99.0, 99.6, 98.4, 98.8)                                                                      # the next candle stays below


def m15(extra=()):
    return raw(GAP + [BREAK, HOLD] + list(extra))


def now_after(bars):
    return int(bars[-1]["time"].timestamp()) + 900


def m5_bar(bars15, o, h, l, c):
    return {"time": bars15[-1]["time"] + timedelta(minutes=10), "open": o, "high": h, "low": l, "close": c, "volume": 10}


def fresh(tmp_path, name="a.json"):
    return ac.AlertState(str(tmp_path / name))


def run(state, bars, m5=(), now=None, lang="en", h1="down"):
    return fl.process(state, "XAUUSD", bars, list(m5), h1, now or now_after(bars), lang, 2)


def test_a_body_close_break_that_holds_is_a_state_flip_with_the_template(tmp_path):
    st = fresh(tmp_path)
    prior = {"market": "XAUUSD", "side": "buy", "extreme": 99.0, "zone": {"kind": "FVG", "low": 100.5, "high": 102}, "phase": "in",
             "created": 1, "done": ""}
    st.setups["XAUUSD-buy-SSL-PDL-1"] = prior
    b = m15()
    got = run(st, b)
    assert [c for c, _, _ in got] == ["STATE_FLIP"]
    t = got[0][2]
    assert "STATE FLIP: BUY ❌ ➔ SELL REVERSAL" in t and "TF: M15 (H1 trend: down)" in t and "do not chase the break candle" in t
    assert "New invalidation: full candle close above 102.00" in t and "not an automatic order" in t
    assert prior["done"] == "flipped"                                                            # the BUY setup on that FVG is finished
    rec = [r for r in st.setups.values() if r.get("kind") == "IFVG"][0]
    assert rec["side"] == "sell" and (rec["zone"]["low"], rec["zone"]["high"]) == (100.5, 102)
    assert run(st, b) == []                                                                      # once per zone, never repeated
    ar = fl.render("FLIP", "XAUUSD", "sell", "M15", "down", "New York", "r", "i", ["t"], {"h1": True, "sweep": False, "session": True, "body_break": True}, "ar")
    assert "🔄 [STATE FLIP: BUY ❌ ➔ SELL REVERSAL ⚠️] — XAUUSD" in ar and "اتجاه H1: هابط" in ar and "وليس أمراً تنفيذياً" in ar


def test_a_wick_through_the_edge_or_an_immediate_close_back_inside_is_not_a_flip(tmp_path):
    wick = raw(GAP + [(103, 103.2, 99.0, 101.0), (101.0, 101.5, 100.6, 101.2)])                  # wicks below 100.5 but closes inside / above
    assert run(fresh(tmp_path), wick) == []
    back = raw(GAP + [BREAK, (99.0, 101.8, 98.9, 101.0)])                                        # the next candle closes back inside the gap
    assert run(fresh(tmp_path, "b.json"), back) == []
    weak = raw(GAP + [(100.9, 101.0, 100.0, 100.3), (100.3, 100.4, 99.9, 100.0)])                # a close below but without displacement
    assert [c for c, _, _ in run(fresh(tmp_path, "c.json"), weak) if c == "STATE_FLIP"] == []
    assert fl.fresh_flips(raw(GAP + [BREAK])) == []                                              # needs one held candle after the break


def test_cooldown_blocks_a_second_flip_on_the_same_market_and_is_remembered(tmp_path):
    path = str(tmp_path / "a.json")
    st = ac.AlertState(path)
    b = m15()
    assert [c for c, _, _ in run(st, b)] == ["STATE_FLIP"]
    again = ac.AlertState(path)                                                                  # restart
    other = fl.process(again, "XAUUSD", b, [], "down", now_after(b), "en", 2)
    assert other == []
    st.setups.clear()                                                                            # the same zone again, as if it were a new one
    st.fired.clear()
    got = run(st, b)
    assert [c for c, _, _ in got] == ["DROP"] and "cooldown" in got[0][2]
    assert run(st, b) == []                                                                      # the note is printed once
    st.meta["flip_cool"]["XAUUSD"] = 0                                                           # after the cooldown it may flip again
    assert [c for c, _, _ in run(st, b)] == ["STATE_FLIP"]


def test_retest_then_a_closed_m5_confirmation_inside_the_zone_with_room(tmp_path, monkeypatch):
    monkeypatch.setattr(fl, "_targets", lambda *a, **k: [{"name": "SSL PDL", "price": 96.0, "type": "SSL", "swept": False},
                                                         {"name": "SSL EQL", "price": 93.0, "type": "SSL", "swept": False}])
    st = fresh(tmp_path)
    b = m15()
    run(st, b)
    touch = m5_bar(b, 99.4, 100.7, 99.3, 100.2)                                                   # back into the gap from below
    got = run(st, b, [touch])
    assert [c for c, _, _ in got] == ["IFVG_RETEST"] and "closed M5 confirmation candle" in got[0][2] and "50% = 101.25" in got[0][2]
    assert run(st, b, [touch]) == []                                                              # once
    weak = m5_bar(b, 101.0, 101.6, 100.9, 101.5)                                                  # a bullish candle: against the SELL
    assert run(st, b, [weak]) == []
    conf = m5_bar(b, 101.5, 101.7, 100.6, 100.9)                                                  # a bearish close inside the zone
    got = run(st, b, [conf])
    assert [c for c, _, _ in got] == ["IFVG_CONFIRMED"]
    t = got[0][2]
    assert "[CONFIRMED]" in t and "Suggested (analysis): entry 101.25" in t and "TP1 96.00" in t and "R:R about" in t and "not an automatic order" in t
    assert run(st, b, [conf]) == []


def test_no_chasing_and_too_little_room_are_dropped_with_a_reason(tmp_path, monkeypatch):
    st = fresh(tmp_path)
    b = m15()
    run(st, b)
    far = m5_bar(b, 90.0, 90.4, 89.6, 90.0)                                                       # ran far below the zone without a retest
    got = run(st, b, [far])
    assert [c for c, _, _ in got] == ["DROP"] and "do not chase" in got[0][2]
    assert [r for r in st.setups.values() if r.get("kind") == "IFVG"][0]["done"] == "missed"
    monkeypatch.setattr(fl, "_targets", lambda *a, **k: [{"name": "SSL", "price": 101.0, "type": "SSL", "swept": False}])
    st2 = fresh(tmp_path, "b.json")
    run(st2, b)
    run(st2, b, [m5_bar(b, 99.4, 100.7, 99.3, 100.2)])
    got = run(st2, b, [m5_bar(b, 101.5, 101.7, 100.6, 100.9)])
    assert [c for c, _, _ in got] == ["DROP"] and "too close" in got[0][2]


def test_a_body_close_back_through_the_ifvg_invalidates_it_once(tmp_path):
    st = fresh(tmp_path)
    b = m15()
    run(st, b)
    up = m15([(98.8, 102.6, 98.7, 102.4)])                                                         # M15 closes above the IFVG's far edge 102
    got = run(st, up)
    assert [c for c, _, _ in got] == ["INVALIDATED"] and "closed" in got[0][2].lower() or "cancelled" in got[0][2]
    assert run(st, up) == []


def test_ifvg_setups_are_ignored_by_the_other_trackers_and_expire(tmp_path):
    import reversal_machine as rm
    st = fresh(tmp_path)
    b = m15()
    run(st, b)
    sid = [k for k, r in st.setups.items() if r.get("kind") == "IFVG"][0]
    assert ac.process(st, "XAUUSD", [], 90.0, now_after(b) + 100) == []                              # not invalidated by the M5 rule
    assert rm.update(st, "XAUUSD", [], now_after(b) + 40 * 900) == [] and not st.setups[sid]["done"]
    got = fl.process(st, "XAUUSD", b, [], "down", now_after(b) + 40 * 900, "en", 2)
    assert [c for c, _, _ in got] == ["DROP"] and st.setups[sid]["done"] == "expired"


def test_track_setups_runs_it_phones_the_alert_logs_drops_and_can_be_switched_off(monkeypatch, tmp_path):
    import ict_alerts
    import rejections
    b = m15()
    sent = []
    monkeypatch.setattr(ict_alerts.selective, "watch_observations", lambda *a, **k: ([], 99.0))
    monkeypatch.setattr(ict_alerts.alert_classes, "deliver", lambda cls, text: sent.append(cls))
    monkeypatch.setattr(ict_alerts.selective, "closed_only", lambda bars, secs, now: bars)
    data = {"m15": b, "m5": [], "h1": []}
    out = ict_alerts.track_setups("XAUUSD", "XAUUSD", data, now_after(b), None, "utc", "en", None, None, 2)
    assert "STATE FLIP" in out and sent == ["STATE_FLIP"]
    monkeypatch.setenv("FLIP_IFVG", "0")
    ict_alerts._ALERT_STATE = None
    monkeypatch.setattr(ict_alerts.alert_classes, "STATE_FILE", str(tmp_path / "other.json"))
    sent.clear()
    assert "STATE FLIP" not in ict_alerts.track_setups("XAUUSD", "XAUUSD", data, now_after(b), None, "utc", "en", None, None, 2)
    assert sent == []


def test_the_new_alert_classes_have_their_own_vibration_and_voice(monkeypatch):
    calls = []
    monkeypatch.setattr(ac.shutil, "which", lambda c: "/bin/" + c)
    monkeypatch.setattr(ac.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    for cls in ("STATE_FLIP", "IFVG_RETEST", "IFVG_CONFIRMED"):
        assert ac.deliver(cls, "x") == ["termux-notification", "termux-tts-speak"]
    vib = {c[c.index("--vibrate") + 1] for c in calls if "--vibrate" in c}
    assert len(vib) == 3 and not vib & {v["vibrate"] for v in ac.CLASSES.values()}               # distinct from the six old classes
