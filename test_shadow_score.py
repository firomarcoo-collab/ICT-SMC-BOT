import json

import shadow_score as ss
import unified_engine as ue

NOW = 1_700_000_000
CHECKS = {"liquidity": True, "bos_mss": True, "fvg_ob": True, "retest": True, "confirm": True, "cost": True}
MAIN = {"side": "buy", "checks": CHECKS, "parts": {"displacement": 5},
        "trends": {"h4": "up", "h1": "up", "m15": "up"}}
PULSE = {"side": "buy", "proxy": True}


def test_full_agreement_scores_100_and_flow_is_estimated():
    r = ss.compute(MAIN, {"pulse": PULSE}, NOW, True)
    assert r["computed"] and r["score"] == 100 and r["flow"] == "estimated"
    assert ss.verdict(r) == "PASS"


def test_no_main_signal_is_not_computed_not_zero():
    r = ss.compute(None, {}, NOW, True)
    assert not r["computed"] and r["score"] is None and ss.verdict(r) == "NOT COMPUTED"


def test_unavailable_data_is_left_out_not_negative():
    main = dict(MAIN, trends={"h4": "unknown", "h1": "unknown", "m15": "up"})
    r = ss.compute(main, {}, NOW, None)
    assert "flow" in r["left_out"] and r["score"] == 100
    assert r["data_share"] < 100


def test_wrong_trend_lowers_score_and_missing_fact_is_zero():
    main = dict(MAIN, trends={"h4": "down", "h1": "down", "m15": "up"}, checks=dict(CHECKS, liquidity=False))
    r = ss.compute(main, {"pulse": PULSE}, NOW, True)
    assert r["score"] < 80 and ss.verdict(r) == "VETO"        # liquidity sweep is a mandatory condition


def test_opposite_module_vetoes_whatever_the_score():
    r = ss.compute(MAIN, {"pulse": {"side": "sell", "proxy": True}}, NOW, True)
    assert ss.verdict(r) == "VETO" and r["vetoes"]


def test_estimated_flow_has_half_the_weight():
    r = ss.compute(MAIN, {"pulse": PULSE}, NOW, True)
    assert r["parts"]["flow"] == (5, 5)


def test_log_writes_only_on_change(tmp_path):
    r = ss.compute(MAIN, {"pulse": PULSE}, NOW, True)
    ss.log("XAUUSD", NOW, r, "WAIT", 0.0)
    ss.log("XAUUSD", NOW + 60, r, "WAIT", 0.0)
    ss.log("XAUUSD", NOW + 120, r, "CONFIRMED BUY", 90.0)
    rows = [json.loads(x) for x in open(ss.log_file())]
    assert len(rows) == 2 and rows[1]["live"] == "CONFIRMED BUY"
    assert ss.summary()["both"] == 1


def test_board_lists_every_market_with_reason_and_shadow():
    txt = ue.board(NOW)
    assert "XAUUSD:" in txt and "EURUSD:" in txt                 # even with no data at all
    assert "Confluence Score: not computed (no main-chain signal)" in txt
    assert "Why:" in txt and "Shadow score" in txt and "Data:" in txt


def test_shadow_never_changes_the_live_decision(monkeypatch):
    monkeypatch.setenv("SHADOW_SCORE", "0")
    assert ue.run_market("XAUUSD", NOW) is None


def test_board_shows_recent_results_and_performance(tmp_path):
    import journal as jn
    jn.append(str(tmp_path / "journal.csv"), {"time_utc": "2026-11-02 10:00", "symbol": "XAUUSD", "side": "buy", "result": "win", "R": "2.00",
                                              "mfe_r": "2", "mae_r": "0.3", "session": "London"})
    txt = ue.board(NOW)
    assert "Recent results: XAUUSD buy win +2.0R" in txt and "Performance: ALL: 1 trades" in txt


def test_board_without_results_says_so():
    assert "Recent results: none finished yet" in ue.board(NOW)
