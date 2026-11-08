"""Offline checks for rct.py and the timeframe support in ict_alerts. Run: python test_rct.py"""
from datetime import datetime, timedelta, timezone

import ict_alerts
import extras
from classic import ema
from ict_alerts import analyze, resample
from ict_smc_bot import Config, to_series
from rct import find_rct, rsi

cfg = Config()
T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


def bar(n, o, h, l, c, step=5):
    return {"time": T0 + timedelta(minutes=step * n), "open": o, "high": h, "low": l, "close": c}


def flip(rows):
    return [(200 - o, 200 - l, 200 - h, 200 - c) for o, h, l, c in rows]


def continuation_rows():
    rows, p = [], 100.0
    for _ in range(260):  # steady uptrend
        rows.append((p, p + 0.3, p - 0.1, p + 0.2))
        p += 0.2
    while True:  # pull back until the low touches the EMA50
        rows.append((p, p + 0.05, p - 1.05, p - 1.0))
        p -= 1.0
        if rows[-1][2] <= ema([r[3] for r in rows], 50) + 0.2:
            break
    high = rows[-1][1]
    rows.append((p, high + 1.5, p - 0.05, high + 1.2))  # resume candle
    return rows


def reversal_rows():
    rows = [(100.0, 100.3, 99.7, 100.1)] * 230
    p = 100.1
    for _ in range(12):  # sharp fall: RSI is stretched
        rows.append((p, p + 0.1, p - 1.1, p - 1.0))
        p -= 1.0
    rows.append((p - 0.2, p + 0.1, p - 2.5, p))  # new low, hammer
    return rows


def series(rows, step=5):
    candles = [bar(n, *row, step=step) for n, row in enumerate(rows)]
    last = rows[-1][3]
    candles.append(bar(len(candles), last, last + 0.1, last - 0.1, last, step=step))  # forming bar
    return candles


def test_continuation_buy_and_sell():
    s = find_rct(to_series(series(continuation_rows())), cfg)
    assert s and s["side"] == "buy" and s["mode"] == "continuation", s
    assert s["sl"] < s["entry"] < s["tp"]
    m = find_rct(to_series(series(flip(continuation_rows()))), cfg)
    assert m and m["side"] == "sell" and m["mode"] == "continuation", m
    assert m["tp"] < m["entry"] < m["sl"]


def test_reversal_buy_and_sell():
    s = find_rct(to_series(series(reversal_rows())), cfg)
    assert s and s["side"] == "buy" and s["mode"] == "reversal", s
    m = find_rct(to_series(series(flip(reversal_rows()))), cfg)
    assert m and m["side"] == "sell" and m["mode"] == "reversal", m


def test_no_signal_without_resume_candle_or_data():
    rows = continuation_rows()[:-1]  # no resume candle: the last bar is still a pullback
    assert find_rct(to_series(series(rows)), cfg) is None
    assert find_rct(to_series(series(continuation_rows()))[-50:], cfg) is None


def test_rsi_extremes():
    assert rsi([float(i) for i in range(40)]) == 100.0
    assert rsi([float(-i) for i in range(40)]) < 1
    assert rsi([1.0, 2.0]) is None


def test_resample_four_hour_buckets():
    hourly = [{"time": datetime(2026, 1, 5, h, tzinfo=timezone.utc), "open": h, "high": h + 1,
               "low": h - 1, "close": h + 0.5} for h in range(0, 8)]
    out = resample(hourly, 14400)
    assert len(out) == 2
    assert (out[0]["open"], out[0]["high"], out[0]["low"], out[0]["close"]) == (0, 4, -1, 3.5)
    assert out[1]["time"].hour == 4 and out[1]["close"] == 7.5


def feed(candles):
    ict_alerts.get_candles = lambda inst, tf="5m": candles


def test_alert_has_timeframe_and_rct_text():
    ict_alerts.INSTRUMENTS["TEST"] = {"source": "test", "id": "TEST", "digits": 2}
    candles = series(continuation_rows())
    feed(candles)
    now = int(candles[-1]["time"].timestamp()) + 8
    _, alerts = analyze("TEST", Config(classic_enabled=False), now, set(), None, None, "5m")
    rct_alerts = [a for a in alerts if a["kind"] == "rct"]
    assert len(rct_alerts) == 1, alerts
    text = rct_alerts[0]["text"]
    assert "RCT ALERT: BUY CONTINUATION TEST" in text and "Timeframe: 5m (scalp)" in text
    assert "Trends: 5m up" in text


def test_hourly_and_daily_timing():
    ict_alerts.INSTRUMENTS["TEST"] = {"source": "test", "id": "TEST", "digits": 2}
    candles = series(continuation_rows(), step=60)  # hourly candles, last is the forming one
    feed(candles)
    forming = int(candles[-1]["time"].timestamp())
    st, al = analyze("TEST", cfg, forming + 600, set(), None, None, "1h")
    assert "ok" in st and any(a["kind"] == "rct" for a in al), (st, al)
    st, al = analyze("TEST", cfg, forming + 3 * 86400, set(), None, None, "1h")
    assert al == [] and "closed" in st
    # a daily feed whose newest candle is today's: no placeholder, the newest is the forming bar
    daily = series(continuation_rows(), step=1440)
    feed(daily)
    newest = int(daily[-1]["time"].timestamp())
    st, _ = analyze("TEST", cfg, newest + 3600, set(), None, None, "1d")
    assert "ok" in st


def test_trend_line_agreement():
    cache = {("X", "1d"): "up", ("X", "4h"): "up", ("X", "5m"): "down"}
    line = extras.trend_line(cache, "X", "5m", "buy")
    assert "1d up | 4h up | 5m down" in line and "2 agree with this BUY" in line
    line = extras.trend_line(cache, "X", "5m", "sell")
    assert "2 against this SELL" in line
    assert extras.trend_line({}, "X", "5m", "buy") == ""


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
