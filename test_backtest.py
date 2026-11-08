"""Offline checks for backtest.py. Run: python test_backtest.py"""
from backtest import simulate, summarize
from ict_smc_bot import Config, find_setup, to_series
from test_logic import make_candles
from test_classic import bar, pullback_series

cfg = Config()


def levels():
    s = find_setup(to_series(make_candles()), cfg, ask=102.9, bid=102.88, digits=2)
    assert s and s["side"] == "buy"
    return s


def extend(candles, extra):
    n = len(candles)
    return candles + [bar(n + k, *row) for k, row in enumerate(extra)]


def test_win_is_plus_reward_to_risk():
    s = levels()
    candles = extend(make_candles(), [
        (102.9, 103.0, s["entry"] - 0.01, s["entry"] + 0.3),        # fills, stop safe
        (s["entry"] + 0.3, s["tp"] + 0.1, s["entry"] + 0.1, s["tp"]),  # reaches the target
    ])
    trades = simulate(candles, cfg, "ict", warmup=30)
    assert len(trades) == 1, trades
    assert abs(trades[0]["R"] - 2.0) < 0.05 and trades[0]["score"] == 1


def test_loss_is_minus_one():
    s = levels()
    candles = extend(make_candles(), [
        (102.9, 103.0, s["entry"] - 0.01, s["entry"] + 0.3),
        (s["entry"] + 0.3, s["entry"] + 0.4, s["sl"] - 0.1, s["sl"]),  # hits the stop
    ])
    trades = simulate(candles, cfg, "ict", warmup=30)
    assert len(trades) == 1 and trades[0]["R"] == -1.0


def test_stop_wins_when_one_bar_touches_both():
    s = levels()
    candles = extend(make_candles(), [
        (102.9, 103.0, s["entry"] - 0.01, s["entry"] + 0.3),
        (s["entry"] + 0.3, s["tp"] + 0.1, s["sl"] - 0.1, s["entry"]),  # touches both
    ])
    trades = simulate(candles, cfg, "ict", warmup=30)
    assert len(trades) == 1 and trades[0]["R"] == -1.0


def test_unfilled_order_is_cancelled():
    candles = extend(make_candles(), [(103.0, 103.5, 102.8, 103.2)] * 6)  # never comes back down
    assert simulate(candles, cfg, "ict", warmup=30, max_wait=3) == []


def test_classic_trade_runs():
    rows = pullback_series()
    # entry is the hammer close (118.2); go down to fill, then up to the target
    entry = 118.2
    candles = extend(rows, [
        (118.5, 118.9, 118.0, 118.7),
        (118.7, 118.8, entry - 0.1, entry + 0.1),     # fills
        (entry + 0.1, entry + 40, entry + 0.05, entry + 30),  # far above any target
    ])
    trades = simulate(candles, cfg, "classic", warmup=230)
    assert len(trades) == 1 and trades[0]["R"] > 0, trades


def test_summary_text():
    text = summarize("x", [{"R": 2.0, "score": 1}, {"R": -1.0, "score": 1}])
    assert "2 trades" in text and "1 wins" in text and "+1.0R" in text
    assert summarize("x", []) == "x: no trades"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
