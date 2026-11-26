from datetime import datetime, timezone

import pytest

import gold_strict as gs

LONDON = int(datetime(2026, 11, 25, 7, 30, tzinfo=timezone.utc).timestamp())     # 10:30 Jordan
NY = int(datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc).timestamp())         # 17:00 Jordan
NIGHT = int(datetime(2026, 11, 25, 1, 0, tzinfo=timezone.utc).timestamp())       # 04:00 Jordan


def test_kill_zones_use_jordan_time():
    assert gs.kill_zone(LONDON)[0] == "London" and gs.kill_zone(NY)[0] == "New York"
    assert gs.kill_zone(NIGHT)[0] is None and "outside" in gs.kill_zone(NIGHT)[1]
    edge = lambda h, m=0: int(datetime(2026, 11, 25, h, m, tzinfo=timezone.utc).timestamp())     # UTC = Jordan - 3
    assert gs.kill_zone(edge(7, 0))[0] == "London" and gs.kill_zone(edge(9, 59))[0] == "London" and gs.kill_zone(edge(10, 0))[0] is None
    assert gs.kill_zone(edge(13, 0))[0] == "New York" and gs.kill_zone(edge(16, 0))[0] is None


def test_fvg_levels_for_both_sides():
    assert gs.fvg_levels((2000.0, 2004.0), "buy") == pytest.approx((2002.0, 2004 - 0.618 * 4))
    assert gs.fvg_levels((2000.0, 2004.0), "sell") == pytest.approx((2002.0, 2000 + 0.618 * 4))


def test_lot_size_formula():
    assert gs.lot_size(10000, 1.0, 2000.0, 1998.5) == 0.67          # risk 100, 15 pips -> 100 / (15 x 10)
    assert gs.lot_size(10000, 1.0, 2000.0, 1999.0) == 1.0           # 10 pips
    assert gs.lot_size(0, 1.0, 2000.0, 1999.0) is None and gs.lot_size(1000, 1.0, 2000.0, 2000.0) is None


def test_valid_buy_plan_inside_a_kill_zone(monkeypatch):
    p = gs.plan("XAUUSD", "buy", (2001.0, 2003.0), 2000.8, LONDON, 10000)
    assert p["ok"] and p["entry_50"] == pytest.approx(2002.0) and p["sl"] == 2000.8
    assert p["tp1"] == pytest.approx(2002.0 + 2 * 1.2) and p["tp2"] == pytest.approx(2002.0 + 5 * 1.2) and p["rr_tp2"] == 5
    assert p["lot"] == gs.lot_size(10000, 1.0, 2002.0, 2000.8) == 0.83 and p["pips_50"] == pytest.approx(12)
    txt = gs.format_plan(p)
    assert "no win rate is guaranteed" in txt and "does not remove drawdown" in txt and "move the stop to break-even" in txt


def test_a_stop_wider_than_15_pips_is_refused_never_tightened():
    p = gs.plan("XAUUSD", "buy", (2001.0, 2002.0), 1999.0, LONDON, 10000)      # entry 50% 2001.5: 25 pips
    assert not p["ok"] and "wider than 15 pips" in p["why"][0] and p["sl"] == 1999.0
    assert gs.format_plan(p).startswith("GOLD STRICT: no signal")


def test_outside_the_kill_zone_is_refused():
    p = gs.plan("XAUUSD", "buy", (2001.0, 2002.0), 2000.5, NIGHT, 10000)
    assert not p["ok"] and "outside the kill zones" in p["why"][0]


def test_htf_liquidity_closer_than_1_to_5_is_refused():
    assert gs.plan("XAUUSD", "buy", (2001.0, 2002.0), 2000.5, LONDON, 10000, htf_liquidity=2004.0)["ok"] is False
    assert gs.plan("XAUUSD", "buy", (2001.0, 2002.0), 2000.5, LONDON, 10000, htf_liquidity=2010.0)["ok"] is True


def test_sell_plan_mirrors_and_price_through_the_stop_is_refused():
    p = gs.plan("XAUUSD", "sell", (2001.0, 2003.0), 2003.2, NY, 10000)
    assert p["ok"] and p["entry_50"] == pytest.approx(2002.0) and p["tp1"] < p["entry_50"] and p["tp2"] < p["tp1"]
    assert not gs.plan("XAUUSD", "sell", (2001.0, 2003.0), 2001.5, NY, 10000)["ok"]          # stop under the entry levels


def test_enabled_only_for_gold_and_only_when_switched_on(monkeypatch):
    monkeypatch.delenv("GOLD_STRICT", raising=False)
    assert not gs.enabled("XAUUSD")
    monkeypatch.setenv("GOLD_STRICT", "1")
    assert gs.enabled("XAUUSD") and not gs.enabled("BTCUSD")


def test_options_change_the_limits(monkeypatch):
    monkeypatch.setenv("GOLD_MAX_SL_PIPS", "30")
    monkeypatch.setenv("GOLD_MIN_RR", "3")
    monkeypatch.setenv("GOLD_RISK_PCT", "0.5")
    p = gs.plan("XAUUSD", "buy", (2001.0, 2002.0), 1999.0, LONDON, 10000)
    assert p["ok"] and p["rr_tp2"] == 3 and p["risk_pct"] == 0.5 and p["lot"] == gs.lot_size(10000, 0.5, 2001.5, 1999.0)


def test_inside_the_bot_gate_and_default_is_untouched(monkeypatch):
    import ict_alerts as ia
    import test_trade_analysis as tta
    m15, h1, h4 = tta.data()
    tfs = {"15m": m15, "1h": h1, "4h": h4}
    monkeypatch.setattr(ia, "get_candles", lambda inst, tf="5m": tfs[tf])
    res = {"market": "XAUUSD", "side": "buy", "votes": {"smc": {"side": "buy", "zone": [2010.0, 2012.0]}, "scalper": {"side": "buy"}, "pulse": {"side": "buy"}},
           "main": {"entry": 2011.0, "sl": 2010.0, "tp1": 2012.0, "tp2": 2013.0, "early_exit": 2009.5, "checks": tta.CHECKS}}
    now = tta.NOW                                                                         # 12:00 UTC = 15:00 Jordan: no kill zone
    monkeypatch.delenv("GOLD_STRICT", raising=False)
    assert ia.analysis_gate("XAUUSD", now, {})(res)[0] is True                              # off: the analysis result is unchanged
    monkeypatch.setenv("GOLD_STRICT", "1")
    ok, text, why = ia.analysis_gate("XAUUSD", now, {})(res)
    assert not ok and "kill zones" in why and "GOLD STRICT" in text
