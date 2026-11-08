"""Offline checks for extras.py. Run: python test_extras.py"""
from datetime import datetime, timezone

import extras


def ts(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


def test_lot_size_gold():
    # $1000 * 0.5% = $5 risk, stop 2.0 away, 100 oz per lot -> 5 / 200 = 0.025 -> 0.02
    lot, money = extras.lot_size(1000, 0.5, 2.0, 100)
    assert lot == 0.02 and money == 5.0


def test_lot_too_small_and_empty_balance():
    assert "below 0.01" in extras.lot_line(100, 0.5, 50, 100)
    assert extras.lot_line(0, 0.5, 2, 100) == ""
    assert extras.lot_size(1000, 0.5, 0, 100) == (0.0, 0.0)


def test_sessions():
    # Wednesday 2026-11-11: London 08-17 and New York 08-17 (winter, fixed hours as fallback too)
    assert extras.active_sessions(ts(2026, 11, 11, 14)) == ["London", "New York"]
    assert extras.active_sessions(ts(2026, 11, 11, 10)) == ["London"]
    assert extras.active_sessions(ts(2026, 11, 11, 19)) == ["New York"]
    assert extras.active_sessions(ts(2026, 11, 11, 3)) == []
    assert extras.active_sessions(ts(2026, 11, 14, 14)) == []  # Saturday
    assert "overlap" in extras.session_text(ts(2026, 11, 11, 14))


def test_parse_news_keeps_high_impact_only():
    data = [{"title": "NFP", "country": "USD", "date": "2026-11-06T08:30:00-05:00", "impact": "High"},
            {"title": "Minor", "country": "USD", "date": "2026-11-06T09:00:00-05:00", "impact": "Low"},
            {"title": "Bad", "country": "USD", "date": "nonsense", "impact": "High"}]
    out = extras.parse_news(data)
    assert len(out) == 1 and out[0]["title"] == "NFP"
    assert out[0]["ts"] == ts(2026, 11, 6, 13, 30)


def test_near_news_by_currency_and_window():
    now = ts(2026, 11, 6, 13, 0)
    ev = [{"ts": now + 20 * 60, "title": "NFP", "currency": "USD"},
          {"ts": now + 20 * 60, "title": "CPI", "currency": "EUR"},
          {"ts": now + 3 * 3600, "title": "Later", "currency": "USD"}]
    assert [e["title"] for e in extras.near_news(ev, "XAUUSD", now)] == ["NFP"]
    assert len(extras.near_news(ev, "EURUSD", now)) == 2
    assert "in 20 min" in extras.news_line(ev, "XAUUSD", now)
    assert extras.news_line(ev, "BTCUSD", now + 6 * 3600) == ""


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
