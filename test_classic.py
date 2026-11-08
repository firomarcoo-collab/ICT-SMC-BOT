"""Offline checks for classic.py. Run: python test_classic.py"""
from datetime import datetime, timedelta, timezone

from classic import (bearish_engulfing, bullish_engulfing, ema, fib_zone, find_classic_setup,
                     hammer, ict_confluence, shooting_star, swing_levels, trend)
from ict_smc_bot import Config, find_setup, to_series
from test_logic import make_candles

cfg = Config()
T0 = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)


def bar(n, o, h, l, c):
    return {"time": T0 + timedelta(minutes=5 * n), "open": o, "high": h, "low": l, "close": c}


def pullback_series(mirror=False):
    """Flat market, a strong leg up to 140, a pullback into the fib zone, then a hammer.
    With mirror=True prices are flipped around 200, which gives the sell version."""
    rows = [(100.0, 100.3, 99.7, 100.1)] * 220
    for m in range(8):  # leg up: 100 -> 140
        o = 100 + 5 * m
        rows.append((o, o + 5.2, o - 0.2, o + 5))
    rows += [
        (140, 140.0, 134.8, 135),
        (135, 135.1, 129.8, 130),
        (130, 130.1, 124.8, 125),
        (125, 125.1, 120.8, 121),
        (121, 121.1, 117.8, 118),
        (117.5, 118.3, 114.0, 118.2),   # hammer inside the fib zone
        (118.2, 118.8, 118.0, 118.5),   # forming bar
    ]
    out = []
    for n, (o, h, l, c) in enumerate(rows):
        if mirror:
            o, h, l, c = 200 - o, 200 - l, 200 - h, 200 - c
        out.append(bar(n, o, h, l, c))
    return out


def test_ema_and_trend():
    assert abs(ema([1, 1, 1, 1], 4) - 1) < 1e-9
    assert ema([1, 2], 4) is None
    assert trend(to_series(pullback_series()), cfg) == "up"
    assert trend(to_series(pullback_series(mirror=True)), cfg) == "down"
    assert trend(to_series(make_candles()), cfg) is None  # not enough candles


def test_fib_zone():
    low, high = fib_zone("buy", 100, 200, cfg)
    assert abs(low - 121.4) < 1e-9 and abs(high - 150) < 1e-9
    low, high = fib_zone("sell", 100, 200, cfg)
    assert abs(low - 150) < 1e-9 and abs(high - 178.6) < 1e-9


def test_candle_patterns():
    bear = {"open": 10, "high": 10.2, "low": 8.8, "close": 9}
    bull = {"open": 8.9, "high": 10.3, "low": 8.8, "close": 10.2}
    assert bullish_engulfing(bear, bull)
    assert not bearish_engulfing(bear, bull)
    assert bearish_engulfing(bull, {"open": 10.3, "high": 10.4, "low": 8.5, "close": 8.7})
    assert hammer({"open": 10.0, "high": 10.1, "low": 7.0, "close": 10.05})
    assert shooting_star({"open": 10.0, "high": 13.0, "low": 9.9, "close": 10.05})
    assert not hammer({"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.9})


def test_swing_levels():
    rows = [bar(n, 100, 101, 99, 100) for n in range(30)]
    rows[19] = bar(19, 100, 105, 99, 100)  # index 10 from the newest: a clear swing high
    levels = swing_levels(to_series(rows), cfg)
    assert levels == [105], levels


def test_ict_confluence_counts_fib_only():
    r = to_series(make_candles())
    setup = find_setup(r, cfg, ask=102.9, bid=102.88)
    assert ict_confluence(r, cfg, setup) == ["fib"]


def test_classic_buy():
    s = find_classic_setup(to_series(pullback_series()), cfg)
    assert s and s["side"] == "buy", s
    assert s["sl"] < s["entry"] < s["tp"]
    assert abs((s["tp"] - s["entry"]) - cfg.rr * (s["entry"] - s["sl"])) < 1e-6


def test_classic_sell():
    s = find_classic_setup(to_series(pullback_series(mirror=True)), cfg)
    assert s and s["side"] == "sell", s
    assert s["tp"] < s["entry"] < s["sl"]


def test_classic_needs_the_reversal_candle():
    rows = pullback_series()
    rows[-2] = bar(len(rows) - 2, 118.0, 118.3, 114.0, 114.5)  # a plain bearish close, no hammer
    assert find_classic_setup(to_series(rows), cfg) is None


def test_classic_needs_enough_candles():
    assert find_classic_setup(to_series(pullback_series())[:50], cfg) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
