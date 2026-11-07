"""Offline checks for ict_alerts.py (no internet). Run: python test_alerts.py"""
import ict_alerts
from ict_alerts import INSTRUMENTS, analyze, parse_binance, parse_yahoo
from ict_smc_bot import Config
from test_logic import make_candles

INSTRUMENTS["TEST"] = {"source": "test", "id": "TEST", "digits": 2}
cfg = Config()


def forming_ts(candles):
    return int(candles[-1]["time"].timestamp())


def test_parse_binance():
    rows = [[1700000000000, "100.5", "101.0", "99.5", "100.8", "12", 1700000299999]]
    c = parse_binance(rows)[0]
    assert (c["open"], c["high"], c["low"], c["close"]) == (100.5, 101.0, 99.5, 100.8)
    assert int(c["time"].timestamp()) == 1700000000


def test_parse_yahoo_skips_empty_bars():
    data = {"chart": {"result": [{
        "timestamp": [1700000000, 1700000300, 1700000600],
        "indicators": {"quote": [{
            "open": [1.0, None, 3.0], "high": [2.0, None, 4.0],
            "low": [0.5, None, 2.5], "close": [1.5, None, 3.5]}]}}]}}
    out = parse_yahoo(data)
    assert len(out) == 2 and out[1]["close"] == 3.5


def test_alert_when_forming_bar_listed():
    candles = make_candles()
    ict_alerts.get_candles = lambda inst: candles
    status, setup, alert = analyze("TEST", cfg, forming_ts(candles) + 8, set())
    assert setup and setup["side"] == "buy", (status, setup)
    assert alert and "BUY LIMIT TEST" in alert and "Entry:" in alert


def test_alert_when_forming_bar_missing():
    candles = make_candles()[:-1]  # the feed has not listed the forming bar yet
    ict_alerts.get_candles = lambda inst: candles
    now_ts = forming_ts(candles) + 300 + 8
    status, setup, alert = analyze("TEST", cfg, now_ts, set())
    assert setup and setup["side"] == "buy", (status, setup)


def test_same_signal_alerts_only_once():
    candles = make_candles()
    ict_alerts.get_candles = lambda inst: candles
    seen = set()
    now_ts = forming_ts(candles) + 8
    assert analyze("TEST", cfg, now_ts, seen)[2] is not None
    assert analyze("TEST", cfg, now_ts, seen)[2] is None


def test_sell_alert():
    candles = make_candles(mirror=True)
    ict_alerts.get_candles = lambda inst: candles
    _, setup, alert = analyze("TEST", cfg, forming_ts(candles) + 8, set())
    assert setup and setup["side"] == "sell" and "SELL LIMIT" in alert


def test_offset_shifts_all_levels():
    candles = make_candles()
    ict_alerts.get_candles = lambda inst: candles
    now_ts = forming_ts(candles) + 8
    _, plain, _ = analyze("TEST", cfg, now_ts, set())
    _, shifted, alert = analyze("TEST", cfg, now_ts, set(), {"TEST": 10.0})
    for key in ("entry", "sl", "tp"):
        assert abs(shifted[key] - plain[key] - 10.0) < 1e-6, key
    assert "offset applied" in alert


def test_stale_data_means_market_closed():
    candles = make_candles()
    ict_alerts.get_candles = lambda inst: candles
    status, setup, alert = analyze("TEST", cfg, forming_ts(candles) + 3600, set())
    assert setup is None and alert is None and "market closed" in status


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
