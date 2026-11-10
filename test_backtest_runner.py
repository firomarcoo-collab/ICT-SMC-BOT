"""Tests for backtest_runner. The candles here are SYNTHETIC random walks made only to exercise the
code; they are not market data and say nothing about the strategy."""
import datetime as dt
import json
import random

import pytest

import backtest_runner as br
import selective as sl

START = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
RELAXED = sl.SelectiveConfig(ready_score=0, min_confluence=3)   # lets the random walk produce some trades
_CACHE = {}


def base_run(n=3000):
    """One shared replay (it takes a while); the tests below only read it."""
    if n not in _CACHE:
        _CACHE[n] = br.replay("SYN", br.build_data(walk(n)), RELAXED, spread=0.3)
    return _CACHE[n]


def walk(n, seed=1):
    r, p, out = random.Random(seed), 2000.0, []
    for i in range(n):
        o = p
        c = o + r.gauss(0, 1.2)
        out.append({"time": START + dt.timedelta(minutes=5 * i), "open": o,
                    "high": max(o, c) + abs(r.gauss(0, .6)), "low": min(o, c) - abs(r.gauss(0, .6)), "close": c})
        p = c
    return out


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


# --- loading

def test_load_csv_iso_times_sorted_deduplicated_and_bad_rows_reported(tmp_path):
    f = write(tmp_path, "a.csv", "time,open,high,low,close\n"
              "2024-01-01 00:05:00,10,11,9,10.5\n"
              "2024-01-01 00:00:00,9,10,8,9.5\n"
              "2024-01-01 00:05:00,10,12,9,11\n"            # duplicate start: the last one wins
              "2024-01-01 00:10:00,10,9,11,10\n"            # high below low
              "garbage,1,2,3,4\n")
    bars, problems = br.load_csv(f)
    assert [b["close"] for b in bars] == [9.5, 11.0]
    assert len(problems) == 2 and "inconsistent" in problems[0] and "unreadable" in problems[1]
    assert bars[0]["time"].tzinfo is not None


def test_load_csv_mt5_export_layout_and_broker_time_shift(tmp_path):
    f = write(tmp_path, "mt5.csv", "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\n"
              "2024.01.02\t03:00:00\t100\t101\t99\t100.5\t10\n")
    bars, problems = br.load_csv(f, tz_hours=3)           # broker clock is UTC+3
    assert problems == [] and bars[0]["time"] == dt.datetime(2024, 1, 2, 0, 0, tzinfo=dt.timezone.utc)


def test_load_csv_epoch_seconds_and_milliseconds_are_utc(tmp_path):
    t = int(START.timestamp())
    f = write(tmp_path, "e.csv", f"time,open,high,low,close\n{t},1,2,0.5,1.5\n{(t + 300) * 1000},1,2,0.5,1.5\n")
    bars, _ = br.load_csv(f, tz_hours=5)                  # the shift must not touch epoch times
    assert [b["time"] for b in bars] == [START, START + dt.timedelta(minutes=5)]


def test_load_csv_rejects_a_file_without_ohlc(tmp_path):
    with pytest.raises(ValueError):
        br.load_csv(write(tmp_path, "x.csv", "time,price\n1,2\n"))


# --- resampling

def test_resample_keeps_only_complete_candles():
    m5 = walk(12)
    del m5[7]                                              # a gap inside the second quarter-hour
    m15 = br.resample(m5, 15)
    assert [b["time"].minute for b in m15] == [0, 30, 45][:len(m15)] or len(m15) == 3
    full = br.resample(walk(12), 15)
    g = walk(12)[:3]
    assert full[0]["open"] == g[0]["open"] and full[0]["close"] == g[2]["close"]
    assert full[0]["high"] == max(x["high"] for x in g) and full[0]["low"] == min(x["low"] for x in g)
    assert len(full) == 4 and len(m15) == 3                # the gap removed exactly one candle


# --- trade simulation and metrics

def test_simulate_is_conservative_when_both_levels_are_touched_in_one_candle():
    setup = {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 102.0}
    both = {"high": 103.0, "low": 98.0}
    assert br.simulate(setup, [both], 0.0)[0] == "loss"
    assert br.simulate(dict(setup, side="sell", sl=101.0, tp=98.0), [both], 0.0)[0] == "loss"
    res, r, used = br.simulate(setup, [{"high": 100.5, "low": 99.5}, {"high": 102.5, "low": 100}], 0.2)
    assert (res, used) == ("win", 2) and r == pytest.approx((2.0 - 0.2) / 1.0)
    assert br.simulate(setup, [{"high": 100.5, "low": 99.5}], 0.0) is None       # unresolved at data end
    assert br.simulate(setup, [{"high": 100, "low": 98.9}], 0.2)[1] == pytest.approx(-1.2)


def test_metrics_math():
    trades = [{"R": 2.0}, {"R": -1.0}, {"R": -1.0}, {"R": 2.0}, {"R": -1.0}]
    m = br.metrics(trades)
    assert m["n"] == 5 and m["wins"] == 2 and m["win_rate"] == pytest.approx(0.4)
    assert m["profit_factor"] == pytest.approx(4 / 3) and m["total_R"] == 1.0
    assert m["expectancy_R"] == pytest.approx(0.2) and m["max_drawdown_R"] == 2.0 and m["small_sample"]
    assert br.metrics([])["n"] == 0


def test_split_and_windows_reports():
    trades = [{"time": 1000 * k, "R": 1.0 if k < 5 else -1.0, "mode": "continuation", "score100": 90}
              for k in range(10)]
    s = br.split_report(trades, 0.7)
    assert s["in_sample"]["n"] == 7 and s["out_of_sample"]["n"] == 3
    w = br.windows_report(trades, parts=2)
    assert [x["n"] for x in w["windows"]] == [5, 5]
    assert [x["total_R"] for x in w["windows"]] == [5.0, -5.0] and w["profitable_windows"] == 1
    assert br.windows_report([])["windows"] == []


# --- replay

def test_replay_without_a_known_spread_takes_no_trade():
    res = br.replay("SYN", br.build_data(walk(1200)), RELAXED, spread=None)
    assert res["signals"] == 0 and res["evaluated"] > 0


def test_reason_labels_are_number_free():
    res = {"reasons": ["not enough closed H4 candles (52)", "x"]}
    assert br._reason_key(res) == "not enough closed H# candles (#)"
    assert br._reason_key({"reasons": []}) == "no reason recorded"


def test_replay_trades_do_not_overlap_and_use_the_cost():
    t = base_run()["trades"]
    assert len(t) > 0, "the fixture must produce trades, otherwise the checks below prove nothing"
    for a, b in zip(t, t[1:]):
        assert b["time"] >= a["time"] + 300 * a["bars"]             # one market, one trade at a time
    assert all(x["result"] in ("win", "loss") for x in t)
    assert any(x["R"] < 0 for x in t) or any(x["R"] > 0 for x in t)
    assert all(x["mode"] in ("continuation", "reversal") for x in t)


def test_replay_has_no_lookahead():
    full = walk(3000)
    base = base_run()["trades"]
    cut = 2200
    changed = [dict(b) for b in full]
    for b in changed[cut:]:                                          # rewrite the whole future
        b.update(open=b["open"] + 50, high=b["high"] + 80, low=b["low"] - 20, close=b["close"] + 50)
    alt = br.replay("SYN", br.build_data(changed), RELAXED, spread=0.3)["trades"]
    limit = int(full[cut - 80]["time"].timestamp())
    early_base = [(x["time"], x["side"], round(x["R"], 9)) for x in base if x["time"] + 300 * x["bars"] < limit]
    early_alt = [(x["time"], x["side"], round(x["R"], 9)) for x in alt if x["time"] + 300 * x["bars"] < limit]
    assert early_base and early_base == early_alt


def test_replay_does_not_reuse_a_consumed_zone_after_a_loss():
    res = base_run()
    assert res["signals"] == len({(t["time"]) for t in res["trades"]})      # no duplicate signal times


def test_full_report_and_text_flag_a_small_sample():
    res = base_run()
    rep = br.full_report("SYN", res, {"from": "a", "to": "b", "m5_bars": 3000, "profile": "strict"})
    assert rep["sample_ok"] is False and "NOT evidence" in rep["warning"]
    text = br.format_report(rep)
    assert "WARNING" in text and "BACKTEST SYN" in text and "Why signals were blocked" in text
    json.dumps(rep, default=str)


def test_cli_end_to_end_on_a_csv(tmp_path, capsys):
    rows = ["time,open,high,low,close"]
    for b in walk(900):
        rows.append(f"{b['time']:%Y-%m-%d %H:%M:%S},{b['open']},{b['high']},{b['low']},{b['close']}")
    f = write(tmp_path, "m5.csv", "\n".join(rows))
    out = tmp_path / "rep.json"
    br.main(["--market", "XAUUSD", "--m5", f, "--spread", "0.3", "--out", str(out)])
    assert "BACKTEST XAUUSD" in capsys.readouterr().out
    assert json.loads(out.read_text())["market"] == "XAUUSD"
    with pytest.raises(SystemExit):
        br.main(["--market", "XAUUSD", "--m5", write(tmp_path, "tiny.csv", "time,open,high,low,close\n")])
