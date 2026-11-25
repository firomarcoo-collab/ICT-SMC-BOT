from datetime import datetime, timezone

import pytest

import session_engine as se


def ts(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


# winter: New York = UTC-5, London = UTC, Tokyo = UTC+9 ; summer: New York = UTC-4, London = UTC+1
def test_table_modes_winter():
    w = lambda m, h, mi=0: se.status(m, ts(2026, 11, 17, h, mi))            # noqa: E731
    assert w("XAUUSD", 3)["mode"] == "breakout" and w("XAUUSD", 3)["session"] == "asia"      # 12:00 Tokyo
    assert w("XAUUSD", 10)["allowed"] and w("XAUUSD", 10)["session"] == "london"
    assert w("US100", 10)["mode"] == "closed" or w("US100", 10)["mode"] == "monitor"
    assert not w("US100", 10)["allowed"]                                    # London: monitor only
    assert w("US100", 15)["allowed"] and w("US100", 15)["session"] == "ny"   # 10:00 New York
    assert w("US30", 14, 40)["mode"] == "monitor" and "first 15 minutes" in w("US30", 14, 40)["reason"]   # 09:40 New York
    assert w("GBPUSD", 10)["allowed"] and not w("GBPUSD", 3)["allowed"]       # Asia: monitor only
    assert w("USDJPY", 3)["mode"] == "monitor"
    assert w("BTCUSD", 3)["breakout"] and not w("BTCUSD", 3)["allowed"]


def test_summer_time_moves_the_utc_hour():
    """09:45 New York is 14:45 UTC in winter and 13:45 UTC in summer: the table follows the clock, not a fixed UTC."""
    winter = se.status("US100", ts(2026, 11, 17, 14, 50))["allowed"]       # 09:50 NY (EST)
    summer_same_utc = se.status("US100", ts(2026, 7, 14, 14, 50))["allowed"]  # 10:50 NY (EDT)
    assert winter and summer_same_utc
    assert not se.status("US100", ts(2026, 7, 14, 13, 40))["allowed"]       # 09:40 NY (EDT): the first 15 minutes
    assert se.status("US100", ts(2026, 7, 14, 13, 50))["allowed"]           # 09:50 NY (EDT)
    assert se.status("US100", ts(2026, 11, 17, 14, 50))["allowed"] and not se.status("US100", ts(2026, 11, 17, 14, 40))["allowed"]


def test_last_30_minutes_and_gaps():
    st = se.status("GBPUSD", ts(2026, 11, 17, 16, 10))                      # London 16:10: the last 30 minutes of 16:30
    assert st["session"] in ("london", "ny")
    st2 = se.status("XAUUSD", ts(2026, 11, 17, 22, 30))                     # 17:30 NY, 07:30 Tokyo next day: between sessions
    assert st2["mode"] == "closed" and not st2["allowed"] and st2["reason"] == "between sessions"


def test_priority_window_and_aliases_and_unknown():
    assert se.status("GBPUSD", ts(2026, 11, 17, 14))["priority"] is True    # 09:00 New York
    assert se.status("NAS100", ts(2026, 11, 17, 15))["market"] == "US100"
    assert se.status("DAX40", ts(2026, 11, 17, 10))["mode"] == "unavailable"
    assert se.block_reason("XAUUSD", ts(2026, 11, 17, 10)) is None
    assert "session gate" in se.block_reason("US30", ts(2026, 11, 17, 10))


def test_no_timezone_database(monkeypatch):
    monkeypatch.setattr(se, "ZoneInfo", None)
    st = se.status("XAUUSD", ts(2026, 11, 17, 10))
    assert st["mode"] == "unavailable" and not st["allowed"] and "tzdata" in st["reason"]
