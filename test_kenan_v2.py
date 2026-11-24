import ict_alerts as ia
import asia_breakout
import selective as sl
import session_alerts
import test_selective as t


def cfg(monkeypatch, v2="1", **env):
    monkeypatch.setenv("KENAN_V2", v2)
    return ia.build_selective_config(dict(env))


def test_v2_overrides_every_profile(monkeypatch):
    for prof in ia.ALL_PROFILES:
        c = cfg(monkeypatch, PROFILE=prof)
        assert c.m15_only and c.session_gate and c.killzones == () and c.blackout == ()
        assert c.ready_score >= 80 and c.rr == 2.0 and c.min_net_rr == 1.6, prof
        assert c.require_sweep and c.require_mss_fvg, prof


def test_v2_off_restores_the_old_defaults(monkeypatch):
    c = cfg(monkeypatch, v2="0", PROFILE="loose")
    assert not c.m15_only and not c.session_gate and c.ready_score < 80


def test_session_gate_off_keeps_utc_killzones(monkeypatch):
    monkeypatch.setenv("SESSION_GATE", "0")
    c = cfg(monkeypatch, PROFILE="strict")
    assert c.m15_only and not c.session_gate


def test_the_six_markets_are_the_default():
    assert ia.DEFAULT_SYMBOLS.split(",") == ["XAUUSD", "US100", "US30", "BTCUSD", "USDJPY", "GBPUSD"]
    assert all(n in ia.INSTRUMENTS for n in ia.DEFAULT_SYMBOLS.split(","))


def test_evaluate_stops_at_the_session_gate(monkeypatch):
    monkeypatch.setattr(sl.session_engine, "block_reason", lambda m, ts: "session gate: US30 is monitor-only in the london session (london)")
    res = sl.evaluate("XAUUSD", t.scenario(), t.NOW, sl.SelectiveConfig(ready_score=0, session_gate=True), spread=.2)
    assert res["action"] == "WAIT" and "session gate" in res["reasons"][-1]
    monkeypatch.setattr(sl.session_engine, "block_reason", lambda m, ts: None)
    res2 = sl.evaluate("XAUUSD", t.scenario(), t.NOW, sl.SelectiveConfig(ready_score=0, session_gate=True), spread=.2)
    assert res2["action"] == "BUY"
    res3 = sl.evaluate("XAUUSD", t.scenario(), t.NOW, sl.SelectiveConfig(ready_score=0, session_gate=False), spread=.2)
    assert res3["action"] == "BUY"


def test_asia_breakout_is_for_gold_and_bitcoin_only():
    assert asia_breakout.enabled("XAUUSD") and asia_breakout.enabled("BTCUSD")
    assert not any(asia_breakout.enabled(m) for m in ("US100", "US30", "USDJPY", "GBPUSD"))


def test_m5_detectors_are_off_by_default_in_v2(monkeypatch):
    monkeypatch.setenv("KENAN_V2", "1")
    assert not session_alerts.on("SCALPER") and not session_alerts.on("PULSE") and session_alerts.on("SMC_PRIME")
    monkeypatch.setenv("SCALPER", "1")
    assert session_alerts.on("SCALPER")
    monkeypatch.setenv("KENAN_V2", "0")
    monkeypatch.delenv("PULSE", raising=False)
    assert session_alerts.on("PULSE")


def test_m15_entry_uses_the_closed_m15_candle():
    old = sl.evaluate("XAUUSD", t.scenario(), t.NOW, sl.SelectiveConfig(ready_score=0), spread=.2)
    new = sl.evaluate("XAUUSD", t.scenario(), t.NOW, sl.SelectiveConfig(ready_score=0, m15_only=True), spread=.2)
    assert old["action"] == "BUY" and "closed M5" in old["report"]["confirm"]
    assert new["action"] == "BUY" and "closed M15" in new["report"]["confirm"] and "closed M5" not in new["report"]["confirm"]
    assert "precision only, never a trigger" in new["report"]["m5_precision"]
