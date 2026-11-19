"""Offline checks for reversal_machine.py, rejections.py: one setup per direction, one direction per market, ordered stages,
expiry, one FINAL, no repeated lines, and the rejection log."""
import json

import alert_classes as ac
import reversal_machine as rm
import rejections


def obs(side="buy", sid=None, stage3=False, zone=None, rel=None, developing=False, extreme=100.0, t=1000):
    return {"sid": sid or f"XAUUSD-{side}-SSL-PDL-{t}", "market": "XAUUSD", "side": side, "level": "PDL", "extreme": extreme,
            "stage3": stage3, "zone": zone, "rel": rel, "developing": developing}


ZONE = {"kind": "FVG", "low": 101.0, "high": 102.0}


def fresh(tmp_path, name="a.json"):
    return ac.AlertState(str(tmp_path / name))


def run(state, observations, now=1000, htf=None, active=()):
    kept, notes = rm.arbitrate(state, "XAUUSD", observations, now, htf, active)
    ac.process(state, "XAUUSD", kept, 101.5, now)
    lines = rm.update(state, "XAUUSD", kept, now)
    return kept, notes, lines


def stages(lines):
    return [t.split(": ", 1)[1].split(" (")[0] for k, _, t in lines if k == "STAGE"]


def test_stages_run_in_order_and_are_never_skipped(tmp_path):
    st = fresh(tmp_path)
    assert stages(run(st, [obs()], 1000)[2]) == ["SWEEP DETECTED"]
    assert stages(run(st, [obs(stage3=True)], 1060)[2]) == ["MSS CONFIRMED", "DISPLACEMENT CONFIRMED"]     # passed through, in order
    assert stages(run(st, [obs(stage3=True, zone=ZONE, rel="far")], 1120)[2]) == ["WAITING FOR FVG/OB RETEST"]
    assert stages(run(st, [obs(stage3=True, zone=ZONE, rel="in")], 1180)[2]) == ["WAITING FOR CANDLE CONFIRMATION"]
    assert run(st, [obs(stage3=True, zone=ZONE, rel="in")], 1240)[2] == []                              # nothing repeats
    assert stages(run(st, [obs(stage3=True, zone=ZONE, rel="far")], 1300)[2]) == ["WAITING FOR FVG/OB RETEST"]  # left the zone
    rec = st.setups[obs()["sid"]]
    assert [h["stage"] for h in rec["history"]][:5] == list(rm.STAGES[:5])
    assert rec["mstage"] == 3                                                                          # never fell below the retest stage


def test_first_sight_at_an_advanced_stage_still_lists_every_stage(tmp_path):
    st = fresh(tmp_path)
    got = stages(run(st, [obs(stage3=True, zone=ZONE, rel="far")], 1000)[2])
    assert got == list(rm.STAGES[:4])


def test_sweep_or_choch_without_displacement_never_advances(tmp_path):
    st = fresh(tmp_path)
    for t in (1000, 1060, 1120):
        assert stages(run(st, [obs(developing=True)], t)[2]) == (["SWEEP DETECTED"] if t == 1000 else [])
    assert st.setups[obs()["sid"]]["mstage"] == 0
    assert not any("FINAL" in l[2] for l in run(st, [obs(developing=True)], 1180)[2])


def test_buy_and_sell_never_lead_together_the_loser_is_suppressed_once(tmp_path):
    st = fresh(tmp_path)
    b, s = obs("buy", stage3=True, zone=ZONE, rel="far"), obs("sell", stage3=False, extreme=110.0)
    kept, notes, lines = run(st, [b, s], 1000)
    assert [o["side"] for o in kept] == ["buy"] and len(notes) == 1 and "SELL setup SUPPRESSED" in notes[0][2] and "BUY setup" in notes[0][2]
    assert all("SELL" not in t for _, _, t in lines)                                                    # no SELL stage lines
    assert run(st, [b, s], 1060)[1] == []                                                              # the note is printed once
    assert st.setups[s["sid"]]["suppressed"]
    # the loser ends silently when its own invalidation is hit
    assert ac.process(st, "XAUUSD", [b], 111.0, 1120) == [] or all(c != "INVALIDATED" or sid != s["sid"] for c, sid, _ in
                                                                  ac.process(st, "XAUUSD", [b], 111.0, 1180))


def test_an_open_signal_suppresses_the_opposite_side_even_if_it_is_more_advanced(tmp_path):
    st = fresh(tmp_path)
    b, s = obs("buy"), obs("sell", stage3=True, zone=ZONE, rel="in", extreme=110.0)
    kept, notes, _ = run(st, [b, s], 1000, active={"buy"})
    assert [o["side"] for o in kept] == ["buy"] and "active BUY signal" in notes[0][2]
    kept, notes, _ = run(fresh(tmp_path, "b.json"), [b, s], 1000, active={"sell"})
    assert [o["side"] for o in kept] == ["sell"]


def test_equal_stages_follow_the_h1_trend_and_a_later_stage_beats_the_trend(tmp_path):
    b, s = obs("buy", t=1000), obs("sell", t=1000, extreme=110.0)
    assert [o["side"] for o in rm.arbitrate(fresh(tmp_path, "1.json"), "XAUUSD", [b, s], 1000, "down")[0]] == ["sell"]
    assert [o["side"] for o in rm.arbitrate(fresh(tmp_path, "2.json"), "XAUUSD", [b, s], 1000, "up")[0]] == ["buy"]
    b2 = obs("buy", stage3=True, zone=ZONE, rel="far")
    assert [o["side"] for o in rm.arbitrate(fresh(tmp_path, "3.json"), "XAUUSD", [b2, s], 1000, "down")[0]] == ["buy"]


def test_one_setup_per_direction_the_older_one_is_superseded(tmp_path):
    st = fresh(tmp_path)
    old, new = obs("buy", t=1000), obs("buy", t=2000)
    st.setups[old["sid"]] = {"market": "XAUUSD", "side": "buy", "extreme": 100.0, "zone": None, "phase": None, "created": 900, "done": ""}
    kept, _, _ = run(st, [old, new], 3000)
    assert [o["sid"] for o in kept] == [new["sid"]] and st.setups[old["sid"]]["done"] == "superseded"
    kept, _, _ = run(st, [obs("buy", t=1000, stage3=True, zone=ZONE, rel="far"), obs("buy", t=2000)], 3100)      # the more advanced wins
    assert kept[0]["sid"].endswith("-1000")


def test_expiry_sweep_without_mss_and_overall_age_once_only(tmp_path, monkeypatch):
    st = fresh(tmp_path)
    run(st, [obs()], 1000)
    lines = rm.update(st, "XAUUSD", [obs()], 1000 + 13 * 900)
    assert [k for k, _, _ in lines] == ["EXPIRED"] and "no MSS" in lines[0][2] and st.setups[obs()["sid"]]["done"] == "expired"
    assert rm.update(st, "XAUUSD", [obs()], 1000 + 14 * 900) == []
    st2 = fresh(tmp_path, "b.json")
    run(st2, [obs(stage3=True, zone=ZONE, rel="far")], 1000)
    assert rm.update(st2, "XAUUSD", [], 1000 + 20 * 900) == []                                         # a setup past the MSS lives longer
    assert [k for k, _, _ in rm.update(st2, "XAUUSD", [], 1000 + 33 * 900)] == ["EXPIRED"]
    monkeypatch.setenv("WAIT_MSS_BARS", "2")
    st3 = fresh(tmp_path, "c.json")
    run(st3, [obs()], 1000)
    assert [k for k, _, _ in rm.update(st3, "XAUUSD", [obs()], 1000 + 3 * 900)] == ["EXPIRED"]


def test_final_marks_one_trail_and_the_state_survives_a_restart(tmp_path):
    path = str(tmp_path / "a.json")
    st = ac.AlertState(path)
    run(st, [obs(stage3=True, zone=ZONE, rel="in")], 1000)
    trail = rm.mark_final(st, "XAUUSD", "buy", 1100)
    assert trail == " > ".join(list(rm.STAGES[:5]) + ["FINAL BUY"])
    st.mark_confirmed("XAUUSD", "buy", 1100)
    again = ac.AlertState(path)                                                                         # the bot restarted
    kept, notes, lines = run(again, [obs(stage3=True, zone=ZONE, rel="in")], 1200)
    assert lines == [] and notes == [] and again.setups[obs()["sid"]]["done"] == "confirmed"
    assert "issued within one scan" in rm.mark_final(fresh(tmp_path, "z.json"), "XAUUSD", "sell", 1)


def test_signal_store_blocks_a_second_or_opposite_signal_by_default(tmp_path):
    import signal_store
    s = signal_store.SignalStore(str(tmp_path / "s.json"), max_active=1, allow_opposite=False)
    setup = lambda side, t: {"side": side, "entry": 1, "sl": 0.5, "tp": 2, "bar_time": t}      # noqa: E731
    assert s.open("XAUUSD", setup("buy", 1000), 1000)[0]
    ok, why = s.open("XAUUSD", setup("sell", 1300), 1300)
    assert not ok and "conflicting" in why
    ok, why = s.open("XAUUSD", setup("buy", 1600), 1600)
    assert not ok and "already has an active" in why
    assert s.open("BTCUSD", setup("sell", 1600), 1600)[0]                                         # another market is independent


def test_rejection_log_writes_changes_only_rotates_and_reads(tmp_path):
    p = str(tmp_path / "rej.log")
    assert rejections.log("XAUUSD", "buy", "SWEEP DETECTED", ["no MSS yet"], 1000, p)
    assert not rejections.log("XAUUSD", "buy", "SWEEP DETECTED", ["no MSS yet"], 1060, p)              # same reason: not repeated
    assert rejections.log("XAUUSD", "buy", "SWEEP DETECTED", ["score 62 < 70"], 1120, p)
    assert rejections.log("BTCUSD", None, "BLOCKED", ["data STALE"], 1180, p)
    rows = rejections.read("xauusd", 10, p)
    assert len(rows) == 2 and rows[0]["reasons"] == ["no MSS yet"] and rows[1]["stage"] == "SWEEP DETECTED"
    assert "BTCUSD - | BLOCKED | data STALE" in rejections.text(rejections.read(None, 10, p))
    assert rejections.text([]) == "No rejection reasons logged yet."
    big = str(tmp_path / "big.log")
    with open(big, "w") as f:
        f.write("x" * (rejections.MAX_BYTES + 10))
    rejections.log("US30", "sell", "NO SETUP", ["a"], 1, big)
    assert (tmp_path / "big.log.1").exists() and json.loads(open(big).read())["market"] == "US30"
    assert rejections.log("US30", "sell", "NO SETUP", ["a"], 1, str(tmp_path / "no" / "dir" / "x.log")) is False   # unwritable: no crash


def test_kenan_signal_shows_the_stage_trail_and_explains_an_htf_conflict():
    import selective as sl
    s = {"side": "buy", "trends": {"h4": "down", "h1": "up"}, "counter_trend": True}
    assert "H4 down is against the BUY" in sl._htf_note(s) and "counter-trend reversal" in sl._htf_note(s)
    assert "both agree" in sl._htf_note({"side": "sell", "trends": {"h4": "down", "h1": "down"}})
    assert "weaker support" in sl._htf_note({"side": "buy", "trends": {"h4": "unknown", "h1": "up"}})


def test_scan_prints_one_direction_only_logs_reasons_and_never_repeats(monkeypatch, tmp_path):
    import ict_alerts
    import signal_store
    ZONE2 = {"kind": "FVG", "low": 101.0, "high": 102.0}
    both = [obs("buy", stage3=True, zone=ZONE2, rel="far"), obs("sell", extreme=110.0)]
    monkeypatch.setattr(ict_alerts.selective, "watch_observations", lambda *a, **k: (both, 101.5))
    monkeypatch.setattr(ict_alerts.alert_classes, "deliver", lambda cls, text: None)
    store = signal_store.SignalStore(str(tmp_path / "s.json"), max_active=1, allow_opposite=False)
    out1 = ict_alerts.track_setups("XAUUSD", "XAUUSD", {}, 1000, None, "utc", "en", store, ["score 62 < 70"])
    assert "BUY: WAITING FOR FVG/OB RETEST" in out1 and "SELL setup SUPPRESSED" in out1 and "SELL: SWEEP" not in out1
    out2 = ict_alerts.track_setups("XAUUSD", "XAUUSD", {}, 1060, None, "utc", "en", store, ["score 62 < 70"])
    assert "STAGE" not in out2 and "SUPPRESSED" not in out2                                           # nothing is printed twice
    rows = rejections.read("XAUUSD", 10, rejections.FILE)
    assert any(r["stage"] == "SUPPRESSED" and r["side"] == "sell" for r in rows)
    assert any(r["stage"] == "WAITING FOR FVG/OB RETEST" and r["reasons"] == ["score 62 < 70"] for r in rows)
    assert not any("FINAL" in r["stage"] for r in rows)
    store.open("XAUUSD", {"side": "sell", "entry": 1, "sl": 2, "tp": 0, "bar_time": 1100}, 1100)          # a SELL signal is now open
    out3 = ict_alerts.track_setups("XAUUSD", "XAUUSD", {}, 1200, None, "utc", "en", store, ["x"])
    assert "BUY setup SUPPRESSED: an active SELL signal is open" in out3
