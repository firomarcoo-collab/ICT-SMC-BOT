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
    assert "🔄 [انقلاب الحالة: شراء ❌ ➔ انعكاس بيع ⚠️] — XAUUSD" in ar and "اتجاه H1: هابط" in ar and "وليس أمراً تنفيذياً" in ar


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
    assert "[CONFIRMED]" in t and "Optimal Entry 101.44 (62.5% depth" in t and "Hard SL" in t and "Early Exit" in t and "TP1 96.00" in t and "break-even" in t and "R:R about" in t and "not an automatic order" in t
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
    monkeypatch.setattr(ict_alerts.alert_classes, "deliver", lambda cls, text, sid=None: sent.append(cls))
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


def test_the_new_alert_classes_have_their_own_vibration_and_no_voice(monkeypatch):
    calls = []
    monkeypatch.setattr(ac.shutil, "which", lambda c: "/bin/" + c)
    monkeypatch.setattr(ac.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    for cls in ("STATE_FLIP", "IFVG_RETEST", "IFVG_CONFIRMED"):
        assert ac.deliver(cls, "x") == ["termux-notification"]                  # the spoken voice is for SMC Prime only
    vib = {c[c.index("--vibrate") + 1] for c in calls if "--vibrate" in c}
    assert len(vib) == 3 and not vib & {v["vibrate"] for v in ac.CLASSES.values()}               # distinct from the six old classes


# ===== IFVG as a final signal, and your ENTER / WAIT / IGNORE decision =====

TARGETS = [{"name": "SSL PDL", "price": 96.0, "type": "SSL", "swept": False}, {"name": "SSL EQL", "price": 93.0, "type": "SSL", "swept": False}]


def confirm_ready(tmp_path, monkeypatch, gate, h1="down", now_shift=0, name="a.json"):
    monkeypatch.setattr(fl, "_targets", lambda *a, **k: TARGETS)
    st = fresh(tmp_path, name)
    b = m15()
    run(st, b)
    run(st, b, [m5_bar(b, 99.4, 100.7, 99.3, 100.2)])
    conf = m5_bar(b, 101.5, 101.7, 100.6, 100.9)
    got = fl.process(st, "XAUUSD", b, [conf], h1, now_after(b) + now_shift, "en", 2, gate=gate)
    return st, got


def test_a_confirmed_ifvg_that_passes_every_gate_becomes_the_signal(tmp_path, monkeypatch):
    seen = []
    st, got = confirm_ready(tmp_path, monkeypatch, lambda plan: (seen.append(plan) or (True, "")))
    assert [c for c, _, _ in got] == ["IFVG_SIGNAL"]
    t = got[0][2]
    assert "IFVG SIGNAL | XAUUSD | SELL" in t and "NOT a win probability" in t and "Signal only" in t and "Status: CONFIRMED" in t
    assert "Score parts:" in t and "Invalidation:" in t and "Your decision: 1) ENTER" in t
    plan = seen[0]
    assert plan["risk"] > 0 and plan["reward"] > plan["risk"] * 2 and plan["setup"]["profile"] == "ifvg" and plan["setup"]["side"] == "sell"
    sid = got[0][1]
    assert st.setups[sid]["plan"]["tp"] == 93.0 and st.setups[sid]["plan"]["tp1"] == 96.0 and st.setups[sid]["plan"]["uses"][0].startswith("ifvg|")
    assert st.meta["pending"][sid]["entry"] == "101.44" and st.setups[sid]["plan"]["market_entry"] is False and st.meta["pending"][sid]["tps"] == ["96.00", "93.00"]
    again = fl.process(st, "XAUUSD", m15(), [m5_bar(m15(), 101.5, 101.7, 100.6, 100.9)], "down", now_after(m15()), "en", 2, gate=lambda p: (True, ""))
    assert again == []                                                                             # one signal per setup


def test_a_low_score_a_late_candle_or_a_closed_gate_keeps_it_analysis_only(tmp_path, monkeypatch):
    st, got = confirm_ready(tmp_path, monkeypatch, lambda plan: (False, "conflicting signal blocked: XAUUSD already has an active BUY"))
    assert [c for c, _, _ in got] == ["IFVG_CONFIRMED", "DROP"] and "NOT issued as a signal: conflicting signal blocked" in got[0][2]
    assert "plan" not in st.setups[got[0][1]]
    st2, late = confirm_ready(tmp_path, monkeypatch, lambda plan: (True, ""), now_shift=600, name="b.json")
    assert [c for c, _, _ in late][0] == "IFVG_CONFIRMED" and "not issued late" in late[0][2]
    st3, against = confirm_ready(tmp_path, monkeypatch, lambda plan: (True, ""), h1="up", name="c.json")
    assert "IFVG_SIGNAL" not in [c for c, _, _ in against] and "under 80" in against[0][2]       # against the H1 trend: stricter
    monkeypatch.setenv("IFVG_MIN_SCORE", "95")
    st4, high = confirm_ready(tmp_path, monkeypatch, lambda plan: (True, ""), name="d.json")
    assert "IFVG_SIGNAL" not in [c for c, _, _ in high] and "under 95" in high[0][2]


def test_score_and_cost_gate_are_plain_numbers():
    conf = {"h1": True, "sweep": True, "session": True, "body_break": True}
    assert fl.ifvg_score(conf, "down", 2.5)[0] == 100
    assert fl.ifvg_score({"h1": False, "sweep": False, "session": False, "body_break": True}, "up", 0.5)[0] == 30
    assert fl.ifvg_score({"h1": False, "sweep": False, "session": True, "body_break": True}, None, 1.2)[0] == 10 + 15 + 15 + 15 + 5
    assert fl.min_score(True) == 70 and fl.min_score(False) == 80

    class Cfg:
        allow_unknown_cost, spread_margin, slippage, max_cost_frac, min_net_rr = False, 1.25, 0.0, 0.25, 1.5
    assert fl.cost_check(1.0, 3.0, 0.1, None, Cfg) == (True, "")
    assert not fl.cost_check(1.0, 3.0, None, None, Cfg)[0]                                         # spread unknown
    assert "of the stop" in fl.cost_check(1.0, 3.0, 0.5, None, Cfg)[1]
    assert "under 1:2" in fl.cost_check(1.0, 1.5, 0.1, None, Cfg)[1]
    assert "net R:R" in fl.cost_check(1.0, 2.0, 0.2, None, Cfg)[1]


def test_decisions_record_ignore_and_answers(tmp_path):
    import decisions
    path = str(tmp_path / "d.csv")
    plan = {"market": "XAUUSD", "side": "sell", "entry": "100.90", "sl": "102.30", "tps": ["96.00", "93.00"], "rr": "5.7",
            "invalid": "above 102.00 (full M15 close)", "wait": "w"}
    assert decisions.record("S1", "enter", plan, 1000, path)["choice"] == "enter"
    assert decisions.record("S2", "wait", plan, 1001, path)["choice"] == "wait"
    assert decisions.record("S3", "ignore", plan, 1002, path)["choice"] == "ignore"
    assert decisions.record("S3", "buy it", plan, 1003, path) is None                              # unknown choice
    assert decisions.ignored(path) == {"S3"}
    decisions.record("S3", "wait", plan, 1004, path)                                               # the latest choice wins
    assert decisions.ignored(path) == set()
    assert [r["choice"] for r in decisions.load(path)] == ["enter", "wait", "ignore", "wait"]
    assert "never trades" in decisions.answer("enter", plan) and "entry 100.90" in decisions.answer("enter", plan)
    assert decisions.answer("wait", plan) == "WAIT: w" and "will not alert" in decisions.answer("ignore", plan)
    assert "NOT YET" in decisions.answer("enter", {"entry": None, "wait": "a closed M5 candle"})
    assert "قرار" in decisions.options_text("S1", "ar") and "python decide.py S1 enter|wait|ignore" in decisions.options_text("S1")
    args = decisions.button_args("A B", python="/py", script="/s/decide.py")
    assert args[0:2] == ["--button1", "ENTER"] and args[3].endswith("'A B' enter") and "--button3-action" in args and decisions.button_args("") == []


def test_decide_cli_reads_the_pending_alert_and_never_touches_the_bot_state(tmp_path, monkeypatch, capsys):
    import decide
    import decisions
    st = fresh(tmp_path, "state.json")
    st.meta["pending"] = {"S1": {"market": "XAUUSD", "side": "sell", "entry": "100.90", "sl": "102.30", "tps": ["96.00"], "wait": "w", "lang": "en"}}
    st.save()
    before = (tmp_path / "state.json").read_text()
    monkeypatch.setattr(ac, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(decisions, "FILE", str(tmp_path / "dec.csv"))
    assert decide.main(["decide.py", "S1", "enter"]) == 0 and "entry 100.90" in capsys.readouterr().out
    assert decide.main(["decide.py", "S1", "ignore"]) == 0
    assert decisions.ignored() == {"S1"} and (tmp_path / "state.json").read_text() == before
    assert decide.main(["decide.py", "NOPE", "enter"]) == 1 and "No pending alert" in capsys.readouterr().out
    assert decide.main(["decide.py", "S1", "maybe"]) == 2


def test_decision_buttons_are_added_only_for_confirmed_style_alerts(monkeypatch):
    calls = []
    monkeypatch.setattr(ac.shutil, "which", lambda c: "/bin/" + c)
    monkeypatch.setattr(ac.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    ac.deliver("IFVG_SIGNAL", "x", "SID1")
    ac.deliver("ZONE_APPROACH", "x", "SID1")
    ac.deliver("IFVG_RETEST", "x")
    assert "--button1" in calls[0] and "--button1" not in calls[2] and calls[0][calls[0].index("--button2") + 1] == "WAIT"
    assert not any(a.startswith("--button") for a in calls[1])                                     # a plain watch alert has no buttons


def test_scan_returns_an_ifvg_signal_with_the_store_id_buttons_and_the_one_open_signal_lock(monkeypatch, tmp_path):
    import ict_alerts
    import signal_store
    import decisions
    b = m15()
    monkeypatch.setattr(ict_alerts.selective, "watch_observations", lambda *a, **k: ([], 99.0))
    monkeypatch.setattr(ict_alerts.selective, "closed_only", lambda bars, secs, now: bars)
    monkeypatch.setattr(fl, "_targets", lambda *a, **k: TARGETS)
    monkeypatch.setattr(decisions, "FILE", str(tmp_path / "dec.csv"))
    store = signal_store.SignalStore(str(tmp_path / "s.json"), max_active=1, allow_opposite=False)
    cfgs = ict_alerts.build_selective_configs({"PROFILE": "scalp"})
    wait = {"action": "WAIT", "reasons": ["x"], "report": {}, "checks": {}}
    monkeypatch.setattr(ict_alerts.selective, "process", lambda *a, **k: wait)
    monkeypatch.setattr(ict_alerts.selective, "data_status", lambda *a, **k: "LIVE")
    monkeypatch.setattr(ict_alerts.data_layer, "check_candles", lambda *a, **k: type("Q", (), {"blocked": False, "reason": ""})())
    retest, conf = m5_bar(b, 99.4, 100.7, 99.3, 100.2), m5_bar(b, 101.5, 101.7, 100.6, 100.9)
    feed = {"m5": [], "m15": b}
    monkeypatch.setattr(ict_alerts, "get_candles", lambda inst, tf: feed.get({"5m": "m5", "15m": "m15"}.get(tf), []))
    now = now_after(b)
    monkeypatch.setenv("IFVG_MIN_SCORE", "60")                                                      # no H1 candles in this feed: H1 neutral = 10 points
    spreads = {"XAUUSD": 0.1}
    ict_alerts.selective_scan("XAUUSD", now, cfgs, store, spreads=spreads)                          # the flip alert
    feed["m5"] = [retest]
    ict_alerts.selective_scan("XAUUSD", now, cfgs, store, spreads=spreads)                          # the retest alert
    feed["m5"] = [conf]
    status, alert, lagging = ict_alerts.selective_scan("XAUUSD", now, cfgs, store, spreads=spreads)
    assert alert is not None and "(IFVG)" in status and alert["setup"]["profile"] == "ifvg" and "IFVG SIGNAL" in alert["text"]
    assert alert["id"] == f"XAUUSD-sell-{alert['setup']['bar_time']}" and alert["id"] in store.active_signals    # the store lock was taken
    assert alert["decision_id"].startswith("XAUUSD-sell-IFVG-") and "python decide.py" in alert["text"]
    assert store.active("XAUUSD")["side"] == "sell"


def test_every_ifvg_alert_lists_completed_missing_and_the_effect_out_of_four(tmp_path, monkeypatch):
    full = {"h1": True, "sweep": True, "session": True, "body_break": True}
    t = fl.confluence_block(full, True)
    assert "[4/4]" in t and "- Missing: none" in t and "full confluence" in t
    part = {"h1": False, "sweep": False, "session": True, "body_break": True}
    t = fl.confluence_block(part, False)
    assert "[1/4]" in t and "Completed: true body break (IFVG)" in t and "Missing: H1 bias, liquidity sweep first, session + confirmation candle" in t
    assert "H1 is not with the trade" in t and "wait for the closed M5 candle" in t
    ar = fl.confluence_block(part, True, "ar")
    assert "[2/4]" in ar and "- المكتملة:" in ar and "- الناقصة:" in ar and "أثر النواقص" in ar
    # the flip, the retest, the confirmation and the invalidation all carry it
    st, got = confirm_ready(tmp_path, monkeypatch, lambda plan: (False, "blocked"))
    assert "- Completed:" in got[0][2] and "- Missing:" in got[0][2] and "- Effect of the missing:" in got[0][2]
    flip = fl.render("FLIP", "XAUUSD", "sell", "M15", "down", "London", "r", "i", ["t"], part)
    assert "[1/4]" in flip and "session + confirmation candle" in flip.split("Missing:")[1]                   # not confirmed yet at the flip
    b = m15()
    st2 = fresh(tmp_path, "i.json")
    run(st2, b)
    up = m15([(98.8, 102.6, 98.7, 102.4)])
    inv = run(st2, up)
    assert [c for c, _, _ in inv] == ["INVALIDATED"] and "- Missing:" in inv[0][2] and "Confluence: [" in inv[0][2]


def test_optimal_entry_is_inside_the_50_75_band_limit_or_market():
    z = {"low": 100.0, "high": 104.0}
    assert fl.optimal_entry(z, "sell", 101.0) == (102.5, False)                                  # 62.5% from the low edge, limit
    assert fl.optimal_entry(z, "sell", 102.4) == (102.4, True)                                   # already deep enough: market
    assert fl.optimal_entry(z, "buy", 103.0) == (101.5, False)
    assert fl.optimal_entry(z, "buy", 101.8) == (101.8, True)


def test_hard_sl_sits_behind_the_sweep_extreme_not_the_ifvg_edge(monkeypatch):
    bars = [{"high": 100.0 + i * 0.1, "low": 99.0 + i * 0.1} for i in range(30)]
    bars[20]["high"] = 109.0                                                                      # the sweep high
    ext = fl.structural_extreme(bars, "sell", 25, 104.0)
    assert ext == 109.0 and fl.hard_sl("sell", ext, 2.0) == 110.0                                # + 0.5 ATR
    assert fl.hard_sl("sell", ext, 2.0) > 104.0 + 0.25 * 2.0                                     # wider than the old edge stop
    bars[20]["low"] = 90.0
    low = fl.structural_extreme(bars, "buy", 25, 99.0)
    assert low == 90.0 and fl.hard_sl("buy", low, 2.0) == 89.0
    monkeypatch.setenv("IFVG_SL_ATR", "1")
    assert fl.hard_sl("sell", 109.0, 2.0) == 111.0
    assert fl.structural_extreme([], "sell", 0, 104.0) == 104.0                                  # never tighter than the zone edge
