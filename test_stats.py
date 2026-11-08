"""Offline checks for stats.py. Run: python test_stats.py"""
import os
import tempfile

import stats

ROWS = [
    {"symbol": "BTCUSD", "timeframe": "5m", "kind": "rct", "result": "win", "R": "2.00"},
    {"symbol": "BTCUSD", "timeframe": "5m", "kind": "ict", "result": "loss", "R": "-1.00"},
    {"symbol": "XAUUSD", "timeframe": "1h", "kind": "rct", "result": "loss", "R": "-1.00"},
    {"symbol": "XAUUSD", "timeframe": "1h", "kind": "rct", "result": "win", "R": "2.00"},
]


def test_totals_and_split():
    out = stats.report(ROWS, "kind")
    assert "ALL: 4 trades | 2 wins | 2 losses | win rate 50% | total +2.0R" in out
    assert "rct: 3 trades | 2 wins | 1 losses" in out and "ict: 1 trades | 0 wins | 1 losses" in out


def test_empty_and_missing_file():
    assert stats.report([]) == "No finished trades yet."
    assert stats.load(os.path.join(tempfile.mkdtemp(), "none.csv")) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
