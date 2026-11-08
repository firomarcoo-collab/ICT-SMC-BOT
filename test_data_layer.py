"""Offline checks for data_layer.py. Run: python test_data_layer.py"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import data_layer as dl

T0 = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # Monday 00:00 UTC


def mk(n, step=300, start=T0, base=100.0, rng=1.0):
    return [{"time": start + timedelta(seconds=step * i), "open": base, "high": base + rng / 2,
             "low": base - rng / 2, "close": base} for i in range(n)]


def now_after(candles, step=300):
    return int(candles[-1]["time"].timestamp()) + step + 20


def test_clean_data_passes():
    c = mk(300)
    rep = dl.check_candles(c, "5m", now_after(c))
    assert not rep.blocked and rep.warnings == [] and rep.summary() == "data ok"


def test_too_few_bars_empty_and_order_problems():
    c = mk(100)
    assert "only 100 bars" in dl.check_candles(c, "5m", now_after(c)).reason
    assert dl.check_candles([], "5m", 0).reason == "no data"
    c = mk(300)
    shuffled = [c[1], c[0]] + c[2:]
    assert "not in time order" in dl.check_candles(shuffled, "5m", now_after(c)).reason
    dup = c + [c[-1]]
    assert "duplicate" in dl.check_candles(dup, "5m", now_after(c)).reason


def test_impossible_ohlc_blocks():
    c = mk(300)
    c[150] = dict(c[150], high=99.0)  # high below the low
    rep = dl.check_candles(c, "5m", now_after(c))
    assert rep.blocked and "impossible OHLC" in rep.reason
    d = mk(300)
    d[10] = dict(d[10], low=-1.0)
    assert "impossible OHLC" in dl.check_candles(d, "5m", now_after(d)).reason


def test_future_timestamps_and_stale_data():
    c = mk(300)
    assert "future" in dl.check_candles(c, "5m", int(c[-1]["time"].timestamp()) - 3 * 3600).reason
    assert "market closed or data delayed" in dl.check_candles(c, "5m", now_after(c) + 3600).reason


def test_missing_candles_warn_and_market_breaks_do_not():
    c = mk(300)
    gapped = c[:200] + c[203:]  # 3 candles missing
    rep = dl.check_candles(gapped, "5m", now_after(c), continuous=True)
    assert not rep.blocked and rep.gaps[0]["missing"] == 3
    assert any("3 missing candles" in w for w in rep.warnings)
    # a weekend-sized hole on a normal market is only information
    weekend = c[:200] + [dict(x, time=x["time"] + timedelta(days=2)) for x in c[200:]]
    rep = dl.check_candles(weekend, "5m", now_after(weekend), continuous=False)
    assert rep.gaps == [] and any("market break" in i for i in rep.info)
    # the same hole on a 24/7 market is missing data
    rep = dl.check_candles(weekend, "5m", now_after(weekend), continuous=True)
    assert rep.gaps and rep.gaps[0]["missing"] > 500


def test_outliers_warn_or_block_only_in_the_last_bars():
    c = mk(300)
    c[299] = dict(c[299], high=100 + 5, low=100 - 5)  # range 10 = x10: strong move
    rep = dl.check_candles(c, "5m", now_after(c))
    assert not rep.blocked and rep.outliers and any("strong move" in w for w in rep.warnings)
    c2 = mk(300)
    c2[299] = dict(c2[299], high=100 + 20, low=100 - 20)  # x40: bad tick
    assert "extreme candle range" in dl.check_candles(c2, "5m", now_after(c2)).reason
    c3 = mk(300)
    c3[250] = dict(c3[250], high=100 + 20, low=100 - 20)  # earlier spike: recorded, not blocking
    rep = dl.check_candles(c3, "5m", now_after(c3))
    assert not rep.blocked and rep.outliers


def test_fingerprint_is_stable_and_changes_with_data():
    c = mk(300)
    a = dl.fingerprint(c, "BTCUSD", "5m", "free-feed", fetched_at=1_800_000_000)
    b = dl.fingerprint(list(reversed(c)), "BTCUSD", "5m", "free-feed", fetched_at=1_900_000_000)
    assert a["sha256"] == b["sha256"] and a["n_bars"] == 300  # order and fetch time do not matter
    c[-1] = dict(c[-1], close=100.5)
    assert dl.fingerprint(c, "BTCUSD", "5m", "free-feed")["sha256"] != a["sha256"]


def test_jsonl_append_and_unwritable_path():
    path = os.path.join(tempfile.mkdtemp(), "log.jsonl")
    assert dl.append_jsonl(path, {"a": 1}) and dl.append_jsonl(path, {"b": "عربي"})
    lines = open(path, encoding="utf-8").read().splitlines()
    assert json.loads(lines[0]) == {"a": 1} and json.loads(lines[1])["b"] == "عربي"
    assert dl.append_jsonl(os.path.join(tempfile.mkdtemp(), "no", "dir", "x.jsonl"), {"a": 1}) is False


class FakeProvider:
    name = "fake"

    def __init__(self, candles, continuous=True):
        self.candles, self._c = candles, continuous

    def fetch(self, symbol, tf):
        return self.candles

    def is_continuous(self, symbol):
        return self._c


def test_load_validated_returns_data_or_nothing():
    c = mk(300)
    got, rep, fp = dl.load_validated(FakeProvider(c), "BTCUSD", "5m", now_after(c))
    assert got is c and not rep.blocked and fp["source"] == "fake" and fp["symbol"] == "BTCUSD"
    got, rep, fp = dl.load_validated(FakeProvider(c), "BTCUSD", "5m", now_after(c) + 7200)
    assert got is None and rep.blocked and fp is None


def test_mt5_provider_is_an_honest_stub_and_offset_helper():
    try:
        dl.MT5Provider().fetch("XAUUSD", "5m")
        assert False
    except NotImplementedError as e:
        assert "Windows" in str(e)
    assert dl.estimate_server_offset_hours(1_000_000_000 + 10800 + 40, 1_000_000_000) == 3.0
    assert dl.estimate_server_offset_hours(1_000_000_000 + 7200 + 1800, 1_000_000_000) == 2.5


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
