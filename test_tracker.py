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


def test_journal_records_mfe_mae_and_reports_per_market(tmp_path):
    import journal
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 11, 16, tzinfo=timezone.utc)
    bar = lambda i, o, h, l, c: {"time": t0 + timedelta(minutes=5 * i), "open": o, "high": h, "low": l, "close": c}   # noqa: E731
    tr = Tracker(str(tmp_path / "results.csv"))
    tr.add("XAUUSD", "m5", "scalp", {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 102.0}, 0, market=True)
    # newest first, [0] forming. Bar 2 goes +0.6R and -0.3R, bar 3 hits the target.
    tr.update("XAUUSD", "m5", [bar(4, 1, 1, 1, 1), bar(3, 101, 102.5, 100.9, 102), bar(2, 100, 100.6, 99.7, 100.3), bar(1, 100, 100.2, 99.9, 100)])
    rows = journal.load(str(tmp_path / "journal.csv"))
    assert len(rows) == 1 and rows[0]["result"] == "win" and float(rows[0]["R"]) == 2.0
    assert float(rows[0]["mae_r"]) == 0.3 and float(rows[0]["mfe_r"]) >= 1.5
    assert (tmp_path / "results.csv").read_text().splitlines()[0] == "time_utc,symbol,timeframe,kind,side,result,R"   # old format kept
    m = journal.metrics([{"R": "2", "mfe_r": "2", "mae_r": "0.3"}, {"R": "-1", "mfe_r": "0.2", "mae_r": "1"},
                         {"R": "-1", "mfe_r": "0.8", "mae_r": "1"}])
    assert m["profit_factor"] == 1.0 and m["max_drawdown_r"] == 2.0 and abs(m["false_signal_rate"] - 100 / 3) < 1e-9
    assert journal.metrics([])["trades"] == 0 and "No finished" in journal.report([])
    assert "too few" in journal.report(rows) and "XAUUSD" in journal.report(rows)


def test_journal_groups_by_session_profile_and_upgrades_an_old_file(tmp_path):
    import journal
    path = str(tmp_path / "journal.csv")
    with open(path, "w", newline="") as f:                                   # an old 12-column journal from before this change
        f.write("time_utc,symbol,timeframe,kind,side,result,R,mfe_r,mae_r,entry,sl,tp\n2026-11-10 10:00,XAUUSD,5m,selective,buy,win,2.00,2.10,0.30,1,0.5,2\n")
    ny_ts = int(datetime(2026, 11, 18, 14, 40, tzinfo=timezone.utc).timestamp())        # 09:40 New York (EST)
    lon_ts = int(datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc).timestamp())          # 04:00 New York
    journal.append(path, {"time_utc": "t", "symbol": "XAUUSD", "timeframe": "5m", "kind": "selective", "side": "sell", "result": "loss",
                          "R": "-1.00", "mfe_r": "0.2", "mae_r": "1.0", "entry_utc": ny_ts, "session": journal.session_of(ny_ts),
                          "profile": "scalp", "mode": "reversal"})
    journal.append(path, {"symbol": "EURUSD", "result": "win", "R": "2.00", "mfe_r": "2.5", "mae_r": "0.4", "entry_utc": lon_ts,
                          "session": journal.session_of(lon_ts), "profile": "forex", "mode": "continuation"})
    rows = journal.load(path)
    assert len(rows) == 3 and rows[0]["symbol"] == "XAUUSD" and rows[0]["session"] == ""      # the old row is kept, new columns empty
    assert journal.session_of(ny_ts) == "New York open" and journal.session_of(lon_ts) == "London"
    txt = journal.report(rows, ("session", "profile"))
    assert "-- by session" in txt and "New York open" in txt and "New York (all): 1 trades" in txt and "London" in txt
    assert "unknown: 1 trades" in txt and "scalp: 1 trades" in txt and "forex: 1 trades" in txt
    assert journal.session_of("nonsense") == "unknown"


def test_tracker_writes_session_profile_and_mode_into_the_journal(tmp_path):
    import journal
    t0 = datetime(2026, 11, 18, 14, 40, tzinfo=timezone.utc)                  # 09:40 New York
    bar = lambda i, o, h, l, c: {"time": t0 + timedelta(minutes=5 * i), "open": o, "high": h, "low": l, "close": c}   # noqa: E731
    tr = Tracker(str(tmp_path / "results.csv"))
    setup = {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 102.0, "bar_time": int(t0.timestamp()), "profile": "scalp", "mode": "reversal"}
    tr.add("XAUUSD", "5m", "selective", setup, int(t0.timestamp()), market=True)
    tr.update("XAUUSD", "5m", [bar(4, 1, 1, 1, 1), bar(2, 101, 102.5, 100.9, 102), bar(1, 100, 100.2, 99.9, 100), bar(0, 100, 100.1, 99.9, 100)])
    row = journal.load(str(tmp_path / "journal.csv"))[0]
    assert (row["session"], row["profile"], row["mode"]) == ("New York open", "scalp", "reversal")
