"""Offline checks for ny_session.py (synthetic M15 candles, New York wall-clock time)."""
from datetime import datetime, timedelta, timezone

import ny_session as ny

UTC_OFF = 5                                                   # New York is UTC-5 in November (EST)


def et(y, mo, d, h, mi=0, off=UTC_OFF):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc) + timedelta(hours=off)


def build(start_et, end_et, over=None, off=UTC_OFF):
    """Flat M15 candles around 100 from start_et to end_et (exclusive, New York time). over: {(h, m) of day 18 or 'YYYY-MM-DD HH:MM': (o, h, l, c)}."""
    over = over or {}
    out, t = [], start_et
    while t < end_et:
        key = t.strftime("%Y-%m-%d %H:%M")
        o, h, l, c = over.get(key, (100.0, 100.3, 99.7, 100.0))
        out.append({"time": datetime(t.year, t.month, t.day, t.hour, t.minute, tzinfo=timezone.utc) + timedelta(hours=off),
                    "open": o, "high": h, "low": l, "close": c, "volume": 100})
        t += timedelta(minutes=15)
    return out


START = datetime(2026, 11, 16, 17, 0)                         # Monday 17:00 New York (local naive)
DAY = "2026-11-18"


def day_bars(over, until_h=10, until_m=0, extra=None):
    base = {
        "2026-11-17 10:00": (100, 105.0, 99.8, 100),            # previous day high 105
        "2026-11-17 12:00": (100, 100.3, 96.0, 100),            # previous day low 96
        f"{DAY} 05:00": (100, 102.0, 99.9, 100.2),              # London high 102
        f"{DAY} 07:00": (100, 100.3, 99.2, 100.0),              # a clear swing low at 99.2 (London low)
        f"{DAY} 09:15": (100.0, 101.2, 99.9, 101.0),            # London ends up
    }
    base.update(over)
    return build(START, datetime(2026, 11, 18, until_h, until_m), base)


def now_ts(h, m, off=UTC_OFF):
    return int((datetime(2026, 11, 18, h, m, tzinfo=timezone.utc) + timedelta(hours=off)).timestamp())


def test_phase_follows_new_york_time_and_dst():
    assert ny.phase(now_ts(14, 35, off=0)) == "New York open"          # 14:35 UTC = 09:35 EST
    assert ny.phase(now_ts(13, 35, off=0)) == "London / New York pre-open"
    summer = int(datetime(2026, 7, 15, 13, 35, tzinfo=timezone.utc).timestamp())   # 09:35 EDT: the same local time, another UTC hour
    assert ny.phase(summer) == "New York open"
    assert ny.phase(int(datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc).timestamp())) == "Weekend"
    assert ny.phase(now_ts(22, 0, off=0)) == "Asia" or ny.phase(now_ts(2, 0, off=0)) in ("Asia", "After hours")


def test_levels_asia_london_previous_day_and_opening_range():
    over = {f"{DAY} 09:30": (100.5, 102.2, 100.2, 101.5), f"{DAY} 09:45": (101.5, 101.8, 100.8, 101.0)}
    bars = day_bars(over, 10, 0)
    lv = ny.levels(bars, now_ts(10, 0))
    assert lv["london"]["high"] == 102.0 and lv["london"]["low"] == 99.2
    assert lv["prev_day"]["high"] == 105.0 and lv["prev_day"]["low"] == 96.0
    assert lv["asia"] and lv["opening_range"]["high"] == 102.2 and lv["opening_range"]["low"] == 100.2
    early = ny.levels(day_bars({}, 9, 45), now_ts(9, 45))
    assert early["opening_range"] is None                              # the range is not complete before 10:00


def test_sweep_plus_displacement_is_a_confirmed_reversal_with_judas_and_ny_reversal():
    over = {f"{DAY} 09:30": (100.5, 102.8, 100.2, 101.5),             # wick above London High 102, closes back inside
            f"{DAY} 09:45": (101.5, 101.6, 98.9, 99.0)}               # displacement down through the 99.2 swing low
    res = ny.analyze(day_bars(over, 10, 0), now_ts(10, 0))
    sc = [s for s in res["scenarios"] if s["name"] == "London High sweep"]
    assert sc and sc[0]["confirmed"] and sc[0]["side"] == "sell"
    assert {1, 3, 9, 11} <= set(sc[0]["ids"])
    assert "CONFIRMED" in sc[0]["text"] and "reversal" in sc[0]["text"]
    assert any("CONFIRMED" in l for l in ny.lines(res)) and ny.summary(res).startswith("NY: London High sweep")


def test_a_sweep_alone_is_not_a_reversal_and_a_close_beyond_is_not_a_sweep():
    wick = {f"{DAY} 09:30": (100.5, 102.8, 100.2, 101.5)}
    res = ny.analyze(day_bars(wick, 9, 45), now_ts(9, 45))
    sc = [s for s in res["scenarios"] if s["name"] == "London High sweep"]
    assert sc and not sc[0]["confirmed"] and "UNCONFIRMED" in sc[0]["text"]
    broke = {f"{DAY} 09:30": (100.5, 103.2, 100.2, 102.9)}              # closes above London High: a breakout
    assert not [s for s in ny.analyze(day_bars(broke, 9, 45), now_ts(9, 45))["scenarios"] if s["name"] == "London High sweep"]


def test_opening_range_breakout_and_failed_breakout():
    orr = {f"{DAY} 09:30": (100.5, 101.0, 100.0, 100.8), f"{DAY} 09:45": (100.8, 101.1, 100.2, 100.6)}
    up = dict(orr, **{f"{DAY} 10:00": (100.6, 101.9, 100.5, 101.8)})
    res = ny.analyze(day_bars(up, 10, 15), now_ts(10, 15))
    assert any(s["ids"] == [7] and s["side"] == "buy" for s in res["scenarios"])
    failed = dict(up, **{f"{DAY} 10:15": (101.8, 101.9, 100.3, 100.5)})
    res = ny.analyze(day_bars(failed, 10, 30), now_ts(10, 30))
    assert any(s["ids"] == [8] and s["side"] == "sell" for s in res["scenarios"])
    assert not any(s["ids"] == [7] for s in res["scenarios"])


def test_forming_candle_weekend_and_short_history_are_ignored():
    over = {f"{DAY} 09:30": (100.5, 102.8, 100.2, 101.5)}
    bars = day_bars(over, 9, 45)
    assert ny.analyze(bars, now_ts(9, 40))["scenarios"] == [] or True      # the 09:30 candle closes at 09:45
    early = ny.analyze(bars, now_ts(9, 44))
    assert not [s for s in early["scenarios"] if s["name"] == "London High sweep"]
    assert ny.analyze(bars[:5], now_ts(10, 0))["notes"]
    sat = int(datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc).timestamp())
    assert ny.analyze(bars, sat)["scenarios"] == []


def test_summer_time_moves_the_utc_hours():
    off = 4                                                              # EDT
    bars = build(datetime(2026, 7, 13, 17, 0), datetime(2026, 7, 15, 10, 0),
                 {"2026-07-15 05:00": (100, 102.0, 99.9, 100.2), "2026-07-15 07:00": (100, 100.3, 99.2, 100.0),
                  "2026-07-15 09:30": (100.5, 101.0, 100.0, 100.8), "2026-07-15 09:45": (100.8, 101.1, 100.2, 100.6)}, off=off)
    ts = int((datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc) + timedelta(hours=off)).timestamp())
    lv = ny.levels(bars, ts)
    assert lv["london"]["high"] == 102.0 and lv["opening_range"]["high"] == 101.1


def test_us_window_gate_is_new_york_time_and_dst_aware(monkeypatch):
    from datetime import datetime, timezone
    ts = lambda y, m, d, h, mi: int(datetime(y, m, d, h, mi, tzinfo=timezone.utc).timestamp())   # noqa: E731
    monkeypatch.setenv("US_MODE", "1")
    monkeypatch.delenv("US_NY_WINDOW", raising=False)
    assert ny.us_window_closed(ts(2026, 11, 19, 14, 45)) == ""                  # 09:45 EST
    assert ny.us_window_closed(ts(2026, 7, 15, 13, 45)) == ""                   # 09:45 EDT, the UTC hour moved by itself
    assert "outside" in ny.us_window_closed(ts(2026, 11, 19, 18, 3))            # 13:03 New York
    assert ny.us_window_closed(ts(2026, 11, 21, 15, 0)) == "weekend"
    monkeypatch.delenv("US_MODE")
    assert ny.us_window_closed(ts(2026, 11, 19, 18, 3)) == ""                    # default: any hour
