"""Offline checks for markets.py. Run: python test_markets.py"""
import json
import os
import tempfile

import extras
import markets
from markets import MarketSpec, cost_in_price, feed_name, load_markets, lot_for_risk


def gold():
    return MarketSpec("XAUUSD", "XAUUSD", broker_symbol="XAUUSD", product_type="spot_cfd",
                      contract_size=100, tick_size=0.01, tick_value=1.0, volume_min=0.01,
                      volume_step=0.01, volume_max=50, spread=0.25, commission_per_lot=0)


def test_unverified_refuses_to_size():
    spec = MarketSpec("US30", "US30")
    assert not spec.verified() and "tick_value" in spec.missing()
    lot, _, reason = lot_for_risk(spec, 1000, 0.5, 100, 99)
    assert lot == 0 and "not verified" in reason


def test_lot_is_rounded_down_to_the_step():
    # $1000 * 0.5% = $5 ; stop 2.00 = 200 ticks * $1 = $200 per lot -> 0.025 -> 0.02
    lot, money, reason = lot_for_risk(gold(), 1000, 0.5, 2000, 1998)
    assert lot == 0.02 and money == 5.0 and reason == ""
    lot, _, _ = lot_for_risk(gold(), 100000, 0.5, 2000, 1998)  # 500/200 = 2.5 lots
    assert lot == 2.5
    capped = gold()
    capped.volume_max = 1.0
    assert lot_for_risk(capped, 100000, 0.5, 2000, 1998)[0] == 1.0


def test_stop_too_wide_for_minimum_lot():
    lot, _, reason = lot_for_risk(gold(), 100, 0.5, 2000, 1990)  # min lot risks $10 of $100
    assert lot == 0 and "stop too wide" in reason and "10.00%" in reason


def test_costs_in_price_units():
    g = gold()
    g.commission_per_lot = 7.0  # $7 per lot round turn = 7 / (1/0.01) = 0.07 price units
    assert abs(cost_in_price(g) - 0.32) < 1e-9
    assert cost_in_price(MarketSpec("X", "X")) is None


def test_load_file_and_aliases():
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    json.dump({"_help": "x", "USTEC": {"broker_symbol": "USTEC", "tick_size": "0.1", "bogus": 5}}, open(path, "w"))
    m = load_markets(path)
    assert m["NAS100"].broker_symbol == "USTEC" and m["NAS100"].tick_size == 0.1
    assert m["XAUUSD"].broker_symbol is None
    assert feed_name("nas100") == "US100" and feed_name("USTEC") == "US100" and feed_name("eurusd") == "EURUSD"
    assert load_markets(os.path.join(tempfile.mkdtemp(), "none.json"))["US30"].name == "US30"


def test_shipped_file_is_valid_and_all_unverified():
    m = load_markets()
    assert set(m) == {"XAUUSD", "NAS100", "US30", "BTCUSD"}
    assert not any(s.verified() for s in m.values())  # nothing is assumed until filled in


def test_lot_text_uses_spec_or_says_it_is_missing():
    setup = {"entry": 2000.0, "sl": 1998.0}
    assert "broker specification)" in extras.lot_text(1000, 0.5, setup, gold())
    assert "not verified" in extras.lot_text(1000, 0.5, setup, MarketSpec("X", "X"))
    assert "YOUR ASSUMPTION" in extras.lot_text(1000, 0.5, setup, None, 100)
    assert extras.lot_text(0, 0.5, setup, gold()) == ""


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
