import backtest_tools as bt

T = [{"R": 2.0, "risk": 1.0}, {"R": -1.0, "risk": 1.0}, {"R": 2.0, "risk": 2.0}, {"R": -1.0, "risk": 0.5}] * 10


def test_costs_reduce_r_by_cost_over_risk():
    out, skipped = bt.apply_costs([{"R": 2.0, "risk": 0.5}], spread=0.1, commission=0.05, slippage=0.05)
    assert skipped == 0 and abs(out[0]["R"] - 1.6) < 1e-9


def test_trade_without_risk_is_counted_not_free():
    out, skipped = bt.apply_costs([{"R": 1.0}, {"R": 1.0, "risk": 0}], spread=0.1)
    assert out == [] and skipped == 2


def test_costs_never_improve_the_result():
    before, after = bt.stats(T), bt.stats(bt.apply_costs(T, spread=0.05)[0])
    assert after["total_r"] < before["total_r"]


def test_oos_is_chronological_and_disjoint():
    a, b = bt.split_oos(T, 0.7)
    assert len(a) == 28 and len(b) == 12 and a + b == T


def test_walk_forward_windows_cover_everything_once():
    w = bt.walk_forward(T, 4)
    assert [x["trades"] for x in w] == [10, 10, 10, 10] and bt.walk_forward(T[:2], 4) == []


def test_report_has_all_sections_and_the_warning():
    txt = bt.honest_report(T, spread=0.05)
    for k in ("Before costs", "After costs", "In-sample", "Out-of-sample", "Walk-forward window 4", "No profit or win rate is promised"):
        assert k in txt
    assert "too few trades" in bt.honest_report(T[:5])


def test_simulated_trades_carry_their_stop_distance():
    import test_backtest as tb
    s = tb.levels()
    candles = tb.extend(tb.make_candles(), [(102.9, 103.0, s["entry"] - 0.01, s["entry"] + 0.3),
                                            (s["entry"] + 0.3, s["entry"] + 0.4, s["sl"] - 0.1, s["sl"])])
    trades = tb.simulate(candles, tb.cfg, "ict", warmup=30)
    assert abs(trades[0]["risk"] - abs(s["entry"] - s["sl"])) < 1e-9
