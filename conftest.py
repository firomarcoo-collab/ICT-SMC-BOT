import pytest


@pytest.fixture(autouse=True)
def isolated_defaults(monkeypatch, tmp_path):
    """Older tests check the classic alert text (the KENAN layout has its own tests that set SIGNAL_FORMAT=kenan), and no test
    may write its alert / zone memory files into the project folder."""
    monkeypatch.setenv("SIGNAL_FORMAT", "classic")
    monkeypatch.setenv("ALERT_LANG", "en")                      # the shipped default is Arabic; Arabic tests set it themselves
    monkeypatch.setenv("ALERT_TEXT_DIR", str(tmp_path / "alert_texts"))
    monkeypatch.setenv("SESSION_DIR", str(tmp_path))
    monkeypatch.setenv("KENAN_V2", "0")             # the older tests pin the pre-v2 defaults; the v2 tests set KENAN_V2=1 themselves
    import session_alerts
    monkeypatch.setattr(session_alerts, "_LOADED", False)
    import alert_classes
    import ict_alerts
    monkeypatch.setattr(alert_classes, "STATE_FILE", str(tmp_path / "alerts_state.json"))
    monkeypatch.setattr(ict_alerts, "_ALERT_STATE", None)
    monkeypatch.setattr(ict_alerts, "ZONE_SEEN_FILE", str(tmp_path / "zone_seen.json"))
    import decisions
    monkeypatch.setattr(decisions, "FILE", str(tmp_path / "decisions.csv"))
    import rejections
    monkeypatch.setattr(rejections, "FILE", str(tmp_path / "rejections.log"))
    monkeypatch.setattr(rejections, "_last", {})
