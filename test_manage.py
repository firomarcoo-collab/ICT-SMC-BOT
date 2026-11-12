"""Trade management alerts: move the stop to entry after a reversal, +1R milestone with add-on hint."""
from datetime import datetime, timedelta, timezone

import tracker

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
BUY = {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 103.0}      # risk 1


def candle(i, o, h, l, c):
    return {"time": T0 + timedelta(minutes=5 * i), "open": o, "high": h, "low": l, "close": c}


def run(tmp_path, setup, candles):
    """Feed candles one closed candle at a time, like the live loop, and collect the messages."""
    tr = tracker.Tracker(str(tmp_path / "r.csv"))
    tr.add("XAUUSD", "5m", "selective", setup, int(T0.timestamp()), ref="XAUUSD", market=True)
    notes = []
    for k, c in enumerate(candles, 1):
        forming = candle(k + 1000, c["close"], c["close"], c["close"], c["close"])
        notes += tr.update("XAUUSD", "5m", [forming] + list(reversed(candles[:k])))
    return notes, tr


def test_reversal_warning_after_profit_gives_back_half(tmp_path):
    notes, _ = run(tmp_path, BUY, [candle(1, 100, 100.8, 99.9, 100.7),      # best +0.8R, close +0.7R
                                   candle(2, 100.7, 100.8, 100.2, 100.3)])  # closes +0.3R (<= half of 0.8)
    assert len(notes) == 1 and notes[0].startswith("REVERSAL WARNING")
    assert "moving the stop to the entry 100" in notes[0] and "تحذير انعكاس" in notes[0]


def test_one_warning_only_and_none_without_enough_profit_or_when_back_under_entry(tmp_path):
    shallow = [candle(1, 100, 100.3, 99.9, 100.2), candle(2, 100.2, 100.3, 100.05, 100.1)]   # best only 0.3R
    assert run(tmp_path, BUY, shallow)[0] == []
    under = [candle(1, 100, 100.8, 99.9, 100.7), candle(2, 100.7, 100.7, 99.6, 99.7)]        # closed below entry
    assert run(tmp_path, BUY, under)[0] == []
    twice = [candle(1, 100, 100.8, 99.9, 100.7), candle(2, 100.7, 100.8, 100.2, 100.3),
             candle(3, 100.3, 100.4, 100.1, 100.15)]
    assert [n.split(":")[0] for n in run(tmp_path, BUY, twice)[0]] == ["REVERSAL WARNING"]


def test_plus_one_r_milestone_mentions_stop_to_entry_and_addon_once(tmp_path):
    notes, tr = run(tmp_path, BUY, [candle(1, 100, 101.3, 99.9, 101.1), candle(2, 101.1, 101.5, 100.9, 101.4)])
    assert len(notes) == 1 and notes[0].startswith("TRADE +1.1R")
    assert "Move the stop to the entry 100" in notes[0] and "add-on" in notes[0] and "NEW closed M5" in notes[0]
    assert tr.items[0]["milestone"] is True


def test_sell_side_mirrors_the_logic(tmp_path):
    sell = {"side": "sell", "entry": 100.0, "sl": 101.0, "tp": 97.0}
    notes, _ = run(tmp_path, sell, [candle(1, 100, 100.1, 99.2, 99.3), candle(2, 99.3, 99.8, 99.2, 99.7)])
    assert len(notes) == 1 and notes[0].startswith("REVERSAL WARNING")
    notes, _ = run(tmp_path, sell, [candle(1, 100, 100.1, 98.8, 98.9)])
    assert notes[0].startswith("TRADE +1.1R")


def test_stop_and_target_still_win_over_management(tmp_path):
    notes, tr = run(tmp_path, BUY, [candle(1, 100, 100.8, 98.9, 99.5)])           # stop hit on the same candle
    assert notes[0].startswith("RESULT: LOSS") and tr.items == []
    notes, _ = run(tmp_path, BUY, [candle(1, 100, 103.2, 100, 103)])              # target hit
    assert notes[0].startswith("RESULT: WIN") and len(notes) == 1


def test_management_alerts_are_marked_so_the_loop_can_notify_without_the_entry_tone():
    assert "TRADE +1.0R: x".startswith(tracker.MANAGE_PREFIXES)
    assert "REVERSAL WARNING: x".startswith(tracker.MANAGE_PREFIXES)
    assert not "RESULT: WIN +2R".startswith(tracker.MANAGE_PREFIXES)
