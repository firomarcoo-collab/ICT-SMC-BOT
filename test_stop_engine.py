import pytest

import stop_engine as se

SPEC = se.ContractSpec(tick_size=0.01, tick_value=1.0, volume_min=0.01, volume_step=0.01, volume_max=50)


def test_classify():
    assert se.classify("EURUSD") == se.AssetClass.FOREX_MAJOR and se.classify("GBPUSD") == se.AssetClass.FOREX_MAJOR
    assert se.classify("GBPJPY") == se.AssetClass.JPY_CROSS and se.classify("USDJPY") == se.AssetClass.JPY_CROSS
    assert se.classify("xauusd") == se.AssetClass.GOLD and se.classify("US100") == se.AssetClass.INDEX


def test_anchor_is_the_most_protective():
    assert se.structural_anchor("buy", 99.0, 98.0) == 98.0 and se.structural_anchor("buy", 99.0, None) == 99.0
    assert se.structural_anchor("sell", 101.0, 102.0) == 102.0
    with pytest.raises(ValueError):
        se.structural_anchor("buy", None, None)


def test_hard_sl_buffer_per_class_and_spread():
    # gold: 0.7 ATR + 1 spread behind the anchor
    assert se.hard_sl("buy", 2000.0, 4.0, 0.3, se.AssetClass.GOLD) == pytest.approx(2000 - 2.8 - 0.3)
    assert se.hard_sl("sell", 2000.0, 4.0, 0.3, se.AssetClass.GOLD) == pytest.approx(2000 + 3.1)
    assert se.hard_sl("buy", 1.1, 0.001, 0.0001, se.AssetClass.FOREX_MAJOR) == pytest.approx(1.1 - 0.0005 - 0.0001)
    assert se.hard_sl("buy", 100, 1, 0.5, se.AssetClass.GOLD) < se.hard_sl("buy", 100, 1, 0.0, se.AssetClass.GOLD)   # wider spread, wider stop


def test_stop_never_on_the_zone_edge():
    with pytest.raises(ValueError):
        se.hard_sl("buy", 100.0, 1, 0, se.AssetClass.GOLD, zone_low=100.0)
    with pytest.raises(ValueError):
        se.hard_sl("sell", 100.0, 1, 0, se.AssetClass.GOLD, zone_high=100.5)


def test_early_invalidation():
    e = se.early_invalidation("buy", 100.0, 101.0)
    assert e.level == 100.0 and se.is_invalidated("buy", e.level, 99.9) and not se.is_invalidated("buy", e.level, 100.0)
    e = se.early_invalidation("sell", 100.0, 101.0, "M5")
    assert e.level == 101.0 and se.is_invalidated("sell", e.level, 101.1) and "M5" in e.rule


def test_risk_multiplier():
    assert se.risk_multiplier(True, True) == 1.0
    assert se.risk_multiplier(False, True) == 0.5 and se.risk_multiplier(True, False) == 0.5 and se.risk_multiplier(False, False) == 0.5


def test_lot_rounds_down_and_respects_budget():
    # balance 1000, 1% = 10 ; stop distance 2.00 = 200 ticks = 200 per lot -> 0.05 lot
    r = se.adjusted_lot_size(1000, 1.0, 100.0, 98.0, SPEC)
    assert r.lot == pytest.approx(0.05) and r.real_risk <= r.risk_money + 1e-9
    assert se.adjusted_lot_size(1000, 1.0, 100.0, 98.0, SPEC, 0.5).lot == pytest.approx(0.02)   # 5 / 200 = 0.025 -> 0.02


def test_lot_risk_is_capped_at_2_percent():
    assert se.adjusted_lot_size(1000, 10.0, 100.0, 98.0, SPEC).risk_money == pytest.approx(20.0)


def test_structure_too_far_for_the_account():
    r = se.adjusted_lot_size(100, 1.0, 100.0, 80.0, SPEC)        # 1 budget vs 2000 per lot: below the minimum lot
    assert r.lot == 0.0 and "too far" in r.reason


def test_net_rr_with_cost():
    assert se.net_rr("buy", 100, 99, 102) == pytest.approx(2.0)
    assert se.net_rr("buy", 100, 99, 102, spread=0.2) == pytest.approx((2 - 0.2) / 1.2)
    assert se.net_rr("sell", 100, 101, 98) == pytest.approx(2.0)
    assert se.net_rr("buy", 100, 99, 99.5) == 0.0 or se.net_rr("buy", 100, 99, 99.5) < 1


def test_breakeven():
    assert se.breakeven_level("buy", 100, 0.1, 0.05) == pytest.approx(100.15)
    assert se.breakeven_level("sell", 100, 0.1) == pytest.approx(99.9)
    assert se.should_move_to_breakeven("buy", 100, 98, 104, 103.0)          # 1.5R
    assert not se.should_move_to_breakeven("buy", 100, 98, 104, 102.9)
    assert se.should_move_to_breakeven("sell", 100, 102, 96, 96.0)          # TP1 touched


def base(**kw):
    a = dict(symbol="XAUUSD", side="buy", entry=2001.0, atr=4.0, spread=0.3, zone_low=2000.0, zone_high=2002.0,
             sweep_extreme=1996.0, swing_extreme=None, tp1=2020.0, sweep_done=True, htf_aligned=True,
             balance=1000, risk_pct=1.0, spec=SPEC)
    a.update(kw)
    return se.plan(**a)


def test_plan_ok():
    p = base()
    assert p.status == "OK" and p.hard_sl == pytest.approx(1996 - 2.8 - 0.3) and p.multiplier == 1.0
    assert p.early.level == 2000.0 and p.lot.lot > 0 and p.net_rr >= 2


def test_plan_half_risk_without_sweep_or_against_htf():
    full, half = base(), base(sweep_done=False)
    assert half.multiplier == 0.5 and half.hard_sl == full.hard_sl           # the stop stays as wide
    assert half.lot.lot < full.lot.lot and any("50%" in r for r in half.reasons)
    assert base(htf_aligned=False).multiplier == 0.5


def test_plan_unbalanced_risk():
    p = base(tp1=2008.0)
    assert p.status == "UNBALANCED RISK" and any("below 1:2" in r for r in p.reasons)


def test_plan_spread_spike_rejected():
    p = base(spread=2.0)                                                     # 0.5 ATR > 0.25 ATR
    assert p.status == "REJECTED" and "spread spike" in p.reasons[-1]


def test_plan_structure_too_far_rejected():
    p = base(sweep_extreme=1970.0, tp1=2100.0)
    assert p.status == "REJECTED" and "too far" in p.reasons[-1]


def test_plan_anchor_inside_zone_rejected():
    p = base(sweep_extreme=2000.5)
    assert p.status == "REJECTED" and "edge" in p.reasons[-1]


def test_plan_without_anchor_rejected():
    assert base(sweep_extreme=None, swing_extreme=None).status == "REJECTED"


def test_plan_lot_too_small_rejected():
    p = base(balance=50)
    assert p.status == "REJECTED" and "too far" in p.reasons[-1]


def test_plan_sell_and_jpy():
    p = se.plan("GBPJPY", "sell", 190.00, 0.30, 0.02, 190.10, 190.40, 190.80, None, 187.50, True, True)
    assert p.hard_sl == pytest.approx(190.80 + 0.7 * 0.30 + 0.02) and p.status == "OK" and p.lot is None


def test_selective_stop_is_structural_and_wider_than_the_old_one():
    import selective as sl
    import test_selective as t
    old = sl.evaluate("XAUUSD", t.scenario(), t.NOW, sl.SelectiveConfig(ready_score=0, structural_stop=False), spread=.2)
    new = sl.evaluate("XAUUSD", t.scenario(), t.NOW, sl.SelectiveConfig(ready_score=0, structural_stop=True), spread=.2)
    assert old["action"] == "BUY" and new["action"] == "BUY"
    assert new["setup"]["sl"] <= old["setup"]["sl"]                  # never tighter
    assert new["setup"]["early_exit"] == new["setup"]["early_exit"] and new["setup"]["risk_scale"] in (0.5, 1.0)


def test_fib_golden_zone_includes_the_50_percent_test():
    import selective as sl
    cfg = sl.make_config("forex")
    assert cfg.require_fib and (cfg.golden_lo, cfg.golden_hi) == (0.50, 0.79)
    assert sl.make_config("golden").require_fib
    assert not sl.make_config("swing").require_fib


def test_fib_check_band_50_to_79(monkeypatch):
    import selective as sl
    monkeypatch.setattr(sl.st, "premium_discount", lambda bars, k: {"direction": "up", "high": 109.0, "low": 100.0})
    # a buy retraces from the high: 50% of the 100-109 leg = 104.5, 79% = 101.89
    assert sl.fib_check([], "buy", 104.5, 0.50, 0.79)[0] is True
    assert sl.fib_check([], "buy", 102.0, 0.50, 0.79)[0] is True
    assert sl.fib_check([], "buy", 106.5, 0.50, 0.79)[0] is False      # ~28%: before the golden zone
    assert sl.fib_check([], "buy", 104.5)[0] is False                  # the old 62-79% band excludes 50%
