import pytest
import requests

import paper_bot as pb


def cfg(tmp_path, **kw):
    return pb.Config(state_file=str(tmp_path / "s.json"), journal_file=str(tmp_path / "j.csv"), slippage_pct=0, fee_pct=0, **kw)


def candles(closes, step=900, start=1_700_000_000):
    return [{"time": start + i * step, "open": c, "high": c + 1, "low": c - 1, "close": c} for i, c in enumerate(closes)]


def test_ema_and_rsi_values():
    assert pb.ema([1, 2, 3, 4], 3) == [None, None, 2.0, 3.0]
    up = pb.rsi(list(range(1, 40)), 14)
    assert up[-1] == 100.0 and up[5] is None
    flat_then_down = pb.rsi([100.0] * 15 + [99.0, 98.0, 97.0], 14)
    assert flat_then_down[-1] < 50


def test_strategy_buy_on_cross_with_rsi_and_sell_on_opposite(tmp_path):
    c = cfg(tmp_path)
    s = pb.Strategy(c)
    down = [100 - i * 0.5 for i in range(40)]
    up_cross = down + [down[-1] + 2 * k for k in range(1, 12)]
    sigs = [s.signal(candles(up_cross[:n])) for n in range(30, len(up_cross) + 1)]
    assert "buy" in sigs and "sell" not in sigs
    up = [100 + i * 0.5 for i in range(40)]
    down_cross = up + [up[-1] - 2 * k for k in range(1, 12)]
    assert "sell" in [s.signal(candles(down_cross[:n])) for n in range(30, len(down_cross) + 1)]
    assert s.signal(candles([100.0] * 10)) is None                         # too little data


def test_risk_levels_size_and_slippage(tmp_path):
    c = cfg(tmp_path)
    c.slippage_pct = 0.1
    r = pb.RiskManager(c)
    sl, tp = r.levels(100.0, "buy")
    assert (sl, tp) == pytest.approx((98.5, 103.0))
    assert r.levels(100.0, "sell") == pytest.approx((101.5, 97.0))
    assert abs(r.size(1000, 100.0, 98.5) - 6.6667) < 1e-3                 # 1% of 1000 = 10 / 1.5 per unit, but capped by balance/entry
    assert r.size(1000, 100.0, 98.5) <= 1000 / 100.0
    assert r.size(1000, 100.0, 100.0) == 0 and r.size(0, 100.0, 99.0) == 0
    assert r.fill_price(100, "buy") > 100 and r.fill_price(100, "sell") < 100                 # entries
    assert r.fill_price(100, "buy", closing=True) < 100 and r.fill_price(100, "sell", closing=True) > 100  # exits


def test_stop_loss_loses_exactly_the_risk_and_take_profit_gains_double(tmp_path):
    c = cfg(tmp_path)
    b = pb.PaperBroker(c, pb.RiskManager(c))
    assert b.open("buy", 100.0, 1)
    loss = b.check_exit({"time": 2, "high": 100.5, "low": 98.0, "close": 99})
    assert loss["reason"] == "stop loss" and abs(b.balance - 990.0) < 0.01          # risk 1% of 1000
    b.open("buy", 100.0, 3)
    win = b.check_exit({"time": 4, "high": 103.5, "low": 99.5, "close": 103})
    assert win["reason"] == "take profit" and abs(b.balance - (990 + 990 * 0.02)) < 0.05
    b.open("sell", 100.0, 5)
    both = b.check_exit({"time": 6, "high": 102.0, "low": 96.0, "close": 100})      # SL and TP in one candle
    assert both["reason"] == "stop loss"                                              # the pessimistic assumption


def test_only_one_position_and_fees_reduce_balance(tmp_path):
    c = cfg(tmp_path)
    c.fee_pct = 0.1
    b = pb.PaperBroker(c, pb.RiskManager(c))
    assert b.open("buy", 100.0, 1) and not b.open("sell", 100.0, 2)
    assert b.balance < 1000


def test_each_candle_is_processed_once_and_state_survives_restart(tmp_path):
    c = cfg(tmp_path)
    down = [100 - i * 0.5 for i in range(40)]
    data = candles(down + [down[-1] + 2 * k for k in range(1, 12)])
    for n in range(30, len(data)):                                          # feed candle by candle like the live loop
        bot = pb.PaperBot(c, feed=None) if n == 30 else bot
        bot.process(data[:n])
        if bot.broker.position:
            break
    assert bot.broker.position and bot.broker.position["side"] == "buy"
    again = bot.process(data[:n])
    assert again == []                                                      # same candles: nothing is repeated
    restarted = pb.PaperBot(c)
    assert restarted.broker.position == bot.broker.position and restarted.broker.last_candle == bot.broker.last_candle


def test_damaged_state_file_is_ignored(tmp_path):
    c = cfg(tmp_path)
    (tmp_path / "s.json").write_text("{not json")
    assert pb.PaperBroker(c, pb.RiskManager(c)).balance == c.start_balance


def test_config_from_env_and_validation(tmp_path):
    c = pb.Config.from_env({"PAPER_SYMBOL": "ETHUSDT", "PAPER_SL_PCT": "2", "PAPER_INTERVAL": "1h"})
    assert (c.symbol, c.sl_pct, c.interval) == ("ETHUSDT", 2.0, "1h")
    for bad in ({"PAPER_INTERVAL": "2m"}, {"PAPER_EMA_FAST": "30"}, {"PAPER_RISK_PCT": "50"}, {"PAPER_SL_PCT": "abc"}):
        with pytest.raises(ValueError):
            pb.Config.from_env(bad)


class FakeResp:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self.rows


def test_feed_drops_the_forming_candle_and_survives_errors(tmp_path):
    c = cfg(tmp_path)
    rows = [[(1_700_000_000 + i * 900) * 1000, "1", "2", "0.5", "1.5", "0"] for i in range(3)]
    now = 1_700_000_000 + 2 * 900 + 100                                      # the third candle is still forming
    class Ok:
        def get(self, *a, **k): return FakeResp(rows)
    assert len(pb.DataFeed(c, Ok()).closed_candles(now=now)) == 2
    class Down:
        def get(self, *a, **k): raise requests.ConnectionError("offline")
    assert pb.DataFeed(c, Down()).closed_candles(now=now) == []
    class Weird:
        def get(self, *a, **k): return FakeResp([["x"]])
    assert pb.DataFeed(c, Weird()).closed_candles(now=now) == []


def test_run_once_survives_a_crashing_feed(tmp_path):
    c = cfg(tmp_path)
    class Boom:
        def closed_candles(self): raise RuntimeError("boom")
    pb.PaperBot(c, feed=Boom()).run(once=True)                              # logged, not raised


def test_no_real_order_code_exists():
    import inspect
    src = inspect.getsource(pb).lower()
    assert "/api/v3/order" not in src and "api_secret" not in src and "post(" not in src
