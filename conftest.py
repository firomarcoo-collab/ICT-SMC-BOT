import pytest


@pytest.fixture(autouse=True)
def isolated_defaults(monkeypatch, tmp_path):
    """Older tests check the classic alert text (the KENAN layout has its own tests that set SIGNAL_FORMAT=kenan), and no test
    may write its alert / zone memory files into the project folder."""
    monkeypatch.setenv("SIGNAL_FORMAT", "classic")
    import alert_classes
    import ict_alerts
    monkeypatch.setattr(alert_classes, "STATE_FILE", str(tmp_path / "alerts_state.json"))
    monkeypatch.setattr(ict_alerts, "_ALERT_STATE", None)
    monkeypatch.setattr(ict_alerts, "ZONE_SEEN_FILE", str(tmp_path / "zone_seen.json"))
    import rejections
    monkeypatch.setattr(rejections, "FILE", str(tmp_path / "rejections.log"))
    monkeypatch.setattr(rejections, "_last", {})
