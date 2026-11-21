"""Twelve Data fallback feed (keyed, free): parsing, key handling, and use only when the primary feed fails."""
import pytest

import ict_alerts as a

ROWS = {"status": "ok", "values": [
    {"datetime": "2026-11-20 14:35:00", "open": "100.5", "high": "101", "low": "100", "close": "100.8", "volume": "0"},
    {"datetime": "2026-11-20 14:30:00", "open": "100", "high": "100.6", "low": "99.9", "close": "100.5"}]}


def test_parse_orders_oldest_first_in_utc():
    c = a.parse_twelvedata(ROWS)
    assert [x["close"] for x in c] == [100.5, 100.8] and c[0]["time"].hour == 14 and c[0]["time"].tzinfo is not None and c[0]["volume"] == 0.0


def test_an_error_answer_raises():
    with pytest.raises(ValueError, match="Twelve Data"):
        a.parse_twelvedata({"status": "error", "message": "bad key"})


def test_no_key_means_no_call_and_the_key_never_reaches_an_error(monkeypatch):
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="not set"):
        a._twelvedata_candles("NDX", "5m")
    monkeypatch.setenv("TWELVEDATA_API_KEY", "SECRETKEY")
    def boom(url):
        raise OSError("failed " + url)
    monkeypatch.setattr(a, "fetch_json", boom)
    with pytest.raises(RuntimeError) as e:
        a._twelvedata_candles("NDX", "5m")
    assert "SECRETKEY" not in str(e.value)


def test_it_is_used_only_after_yahoo_fails(monkeypatch):
    monkeypatch.setenv("TWELVEDATA_API_KEY", "k")
    calls = []
    def fake(url):
        calls.append(url)
        if "twelvedata" in url:
            return ROWS
        raise OSError("yahoo down")
    monkeypatch.setattr(a, "fetch_json", fake)
    a._CACHE.clear(); a._STICKY.clear()
    inst = {"source": "yahoo", "id": "NQ=F", "digits": 1, "fallbacks": ["twelvedata:NDX"]}
    c = a._fetch_candles(inst, "5m")
    assert c[-1]["close"] == 100.8 and "symbol=NDX" in calls[-1] and "interval=5min" in calls[-1] and "timezone=UTC" in calls[-1]
    assert a._STICKY["NQ=F"][0] == "twelvedata:NDX"
