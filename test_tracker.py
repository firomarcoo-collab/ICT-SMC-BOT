"""Offline checks for tracker.py. Run: python test_tracker.py"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

from tracker import MAX_WAIT_BARS, Tracker

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
BUY = {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 102.0}
SELL = {"side": "sell", "entry": 100.0, "sl": 101.0, "tp": 98.0}


def bar(n, o, h, l, c):
    return {"time": T0 + timedelta(minutes=5 * n), "open": o, "high": h, "low": l, "close": c}


def series(rows, start=1):
    """candles newest first with a dummy forming bar on top; bar numbers start at `start`"""
    bars = [bar(start + i, *row) for i, row in enumerate(rows)]
    forming = bar(start + len(rows), 100, 100.1, 99.9, 100)
    return [forming] + list(reversed(bars))


def tracker():
    path = os.path.join(tempfile.mkdtemp(), "results.csv")
    return Tracker(path), path


def test_buy_win():
    t, path = tracker()
    t.add("X", "5m", "ict", BUY, int(T0.timestamp()))
    notes = t.update("X", "5m", series([(101, 101.2, 99.9, 100.5), (100.5, 102.5, 100.4, 102.2)]))
    assert len(notes) == 1 and "WIN +2.0R" in notes[0] and "1 trades, 1 wins, 0 losses (win rate 100%, +2.0R)" in notes[0]
    assert t.items == []
    assert "win" in open(path).read()


def test_buy_loss_and_stop_first_when_both_touched():
    t, _ = tracker()
    t.add("X", "5m", "ict", BUY, int(T0.timestamp()))
    notes = t.update("X", "5m", series([(101, 101.2, 99.9, 100.5), (100.5, 102.5, 98.9, 100)]))
    assert len(notes) == 1 and "LOSS -1R" in notes[0]


def test_fill_bar_ignores_the_target():
    t, _ = tracker()
    t.add("X", "5m", "ict", BUY, int(T0.timestamp()))
    # the fill bar reaches both the entry and the target: only the stop counts, so still open
    assert t.update("X", "5m", series([(101, 102.5, 99.9, 102.2)])) == []
    assert t.items[0]["state"] == "open"


def test_sell_win():
    t, _ = tracker()
    t.add("X", "5m", "rct", SELL, int(T0.timestamp()))
    notes = t.update("X", "5m", series([(99, 100.1, 98.9, 99.5), (99.5, 99.6, 97.8, 98)]))
    assert len(notes) == 1 and "WIN" in notes[0] and "SELL" in notes[0]


def test_not_filled_is_cancelled_and_other_symbols_untouched():
    t, _ = tracker()
    t.add("X", "5m", "ict", BUY, int(T0.timestamp()))
    t.add("Y", "5m", "ict", BUY, int(T0.timestamp()))
    away = [(105, 105.2, 104.9, 105)] * MAX_WAIT_BARS
    notes = t.update("X", "5m", series(away))
    assert len(notes) == 1 and "NOT FILLED" in notes[0]
    assert [i["name"] for i in t.items] == ["Y"]


def test_old_bars_are_ignored_and_tally_survives_restart():
    t, path = tracker()
    t.add("X", "5m", "ict", BUY, int(T0.timestamp()) + 5 * 60 * 10)  # signal came after these bars
    assert t.update("X", "5m", series([(101, 101.2, 99.9, 100.5), (100.5, 102.5, 100.4, 102.2)])) == []
    t2, path2 = tracker()
    t2.add("X", "5m", "ict", BUY, int(T0.timestamp()))
    t2.update("X", "5m", series([(101, 101.2, 99.9, 100.5), (100.5, 102.5, 100.4, 102.2)]))
    again = Tracker(path2)
    assert (again.wins, again.losses) == (1, 0) and abs(again.total_r - 2.0) < 1e-9


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
