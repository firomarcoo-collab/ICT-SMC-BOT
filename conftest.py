import pytest


@pytest.fixture(autouse=True)
def classic_signal_format_by_default(monkeypatch):
    """The older tests check the classic alert text; the KENAN layout has its own tests that set SIGNAL_FORMAT=kenan."""
    monkeypatch.setenv("SIGNAL_FORMAT", "classic")
