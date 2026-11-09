"""Robustness / safety tests added after the full audit. Run: python test_audit.py"""
import contextlib
import io
import random
import time
from datetime import timedelta

import ict_alerts
import selective as sl
import signal_store
from test_selective import CFG, NOW, bar, retime, scenario, tmp_store

SECONDS = {"m5": 300, "m15": 900, "h1": 3600, "h4": 14400, "d1": 86400, "m1": 60}
GETTER = {"5m": "m5", "15m": "m15", "1h": "h1", "4h": "h4"}


def random_walk(rng, n, sec, vol, flat=False, zero_range=False):
    out, p = [], 1000.0
    for _ in range(n):
        c = p if flat else p + rng.gauss(0, vol)
        wick = 0 if zero_range else abs(rng.gauss(0, vol))
        out.append(bar(p, max(p, c) + wick, min(p, c) - wick, c))
        p = c
    return retime(out, sec)


def test_random_flat_and_zero_range_data_never_crash_the_engine():
    rng = random.Random(7)
    for _ in range(120):
        vol = rng.choice([0.0001, 0.05, 1, 30])
        flat, zero = rng.random() < .15, rng.random() < .15
        data = {k: random_walk(rng, n, SECONDS[k], vol, flat, zero)
                for k, n in (("m5", 120), ("m15", 120), ("h1", 120), ("h4", 80), ("d1", 40), ("m1", 80))}
        res = sl.evaluate("FUZZ", data, NOW, CFG, spread=rng.choice([None, 0, .2, 5]))
        assert res["action"] in ("BUY", "SELL", "WAIT")
        sl.explain(res)
        if res["setup"]:
            assert res["setup"]["sl"] != res["setup"]["entry"]
            sl.format_signal("FUZZ", res)
            sl.format_signal_ar("FUZZ", res)


def test_future_candles_never_change_a_decision():
    d = scenario()
    before = sl.evaluate("X", d, NOW, CFG, spread=.2)
    longer = {}
    for k in ("m5", "m15", "h1", "h4"):
        last = d[k][-1]
        longer[k] = d[k] + [dict(last, time=last["time"] + timedelta(seconds=SECONDS[k] * i),
                                 open=last["close"] + i * 9, high=last["close"] + i * 9 + 3,
                                 low=last["close"] + i * 9 - 3, close=last["close"] + i * 9 + 2)
                            for i in range(1, 6)]
    after = sl.evaluate("X", longer, NOW, CFG, spread=.2)
    assert before["action"] == after["action"] == "BUY" and before["setup"] == after["setup"]


def test_duplicate_candles_count_once():
    d = scenario()
    dup = dict(d, m5=d["m5"] + [d["m5"][-1]], m15=d["m15"][:50] + d["m15"][49:])
    assert len(sl.closed_only(dup["m5"], 300, NOW)) == len(d["m5"])
    assert sl.evaluate("X", dup, NOW, CFG, spread=.2)["action"] == sl.evaluate("X", d, NOW, CFG, spread=.2)["action"]


def test_candle_cache_shares_one_download():
    calls = []
    saved = ict_alerts._fetch_candles
    ict_alerts._fetch_candles = lambda inst, tf="5m": calls.append(tf) or [bar(1, 2, 0, 1)]
    ict_alerts._CACHE.clear()
    inst = {"source": "yahoo", "id": "CACHE-TEST", "digits": 2}
    try:
        a = ict_alerts.get_candles(inst, "5m")
        a.append("mutated")                           # a caller changing its copy must not poison the cache
        b = ict_alerts.get_candles(inst, "5m")
        assert len(calls) == 1 and b == [bar(1, 2, 0, 1)]
        ict_alerts.get_candles(inst, "15m")           # another timeframe is a separate download
        assert calls == ["5m", "15m"]
        ict_alerts._CACHE[("yahoo", "CACHE-TEST", "5m")] = (time.time() - 100, [bar(1, 2, 0, 1)])
        ict_alerts.get_candles(inst, "5m")             # older than the TTL: downloaded again
        assert calls == ["5m", "15m", "5m"]
    finally:
        ict_alerts._fetch_candles = saved
        ict_alerts._CACHE.clear()


def serve(data):
    ict_alerts.get_candles = lambda inst, tf="5m": data[GETTER[tf]] if tf in GETTER else (_ for _ in ()).throw(
        RuntimeError("optional"))


def test_selective_scan_blocks_bad_data_and_news_and_adds_the_lot():
    d = scenario()
    serve(d)
    ok = lambda **kw: ict_alerts.selective_scan("XAUUSD", NOW, CFG, tmp_store()[0], None, {"XAUUSD": .2}, None,  # noqa: E731
                                                "en", **kw)
    status, alert, _ = ok()
    assert alert and "Lot:" not in alert["text"]                       # no balance: no lot line
    status, alert, _ = ok(balance=1000.0, risk_percent=0.5, contracts={"XAUUSD": 100.0})
    assert alert and "Lot:" in alert["text"]
    # broken candle (high below the close) in M5: blocked by the data-quality gate
    bad = [dict(c) for c in d["m5"]]
    bad[-5] = dict(bad[-5], high=bad[-5]["close"] - 1)
    serve(dict(d, m5=bad))
    status, alert, _ = ok()
    assert alert is None and "data quality" in status and "impossible OHLC" in status
    serve(d)
    event = [{"ts": NOW + 600, "title": "NFP", "currency": "USD"}]
    status, alert, _ = ok(news=event, news_block=True)
    assert alert is None and "high-impact news" in status
    assert ok(news=event, news_block=False)[1] is not None             # blocking switched off
    assert ok(news=[], news_block=True)[1] is not None                 # nothing scheduled


def test_daily_risk_guard_stops_after_two_losses_in_a_row():
    store, _ = tmp_store()
    day = 1_800_000_000

    def finish(market, outcome, t):
        store.open(market, {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 102.0, "bar_time": t}, t)
        store.close(market, outcome, t + 60)

    assert store.halted(day) == ""
    finish("XAUUSD", "loss", day)
    assert store.halted(day + 200) == ""                               # one loss: still allowed
    finish("BTCUSD", "loss", day + 300)                                # the second, on another market
    assert "2 losses in a row" in store.halted(day + 700)
    assert store.halted(day + 86400 + 10) == ""                        # next UTC day: allowed again
    again = signal_store.SignalStore(store.path)                       # and it survives a restart
    assert "daily risk guard" in again.halted(day + 700)
    finish("NAS100", "win", day + 800)                                 # a win breaks the streak
    assert store.halted(day + 1000) == ""
    big, _ = tmp_store()
    for i in range(3):                                                 # 3R lost without a streak of 2
        big.results.append({"t": day + i, "outcome": "loss", "R": -1.0, "market": "X"})
        if i == 1:
            big.results.append({"t": day + i, "outcome": "win", "R": 0.0, "market": "X"})
    assert "-3.0R" in big.halted(day + 100, max_losses=0, max_loss_r=3.0)
    assert big.halted(day + 100, max_losses=2, max_loss_r=0) == ""     # streak is only 1 after the win, R limit off


def test_process_refuses_new_signals_when_the_daily_guard_is_on():
    store, _ = tmp_store()
    for market in ("XAUUSD", "BTCUSD"):
        store.open(market, {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 102.0, "bar_time": 1}, NOW - 900)
        store.close(market, "loss", NOW - 800)
    res = sl.process("NAS100", scenario(), NOW, store, CFG, spread=.2)
    assert res["action"] == "WAIT" and any("daily risk guard" in r for r in res["reasons"])
    off = sl.SelectiveConfig(max_daily_losses=0, max_daily_loss_r=0)
    assert sl.process("NAS100", scenario(), NOW, store, off, spread=.2)["action"] == "BUY"
    assert store.results[0]["outcome"] == "loss" and store.results[0]["R"] == -1.0


def test_win_is_recorded_with_its_r_multiple():
    store, _ = tmp_store()
    store.open("XAUUSD", {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 102.0, "bar_time": 1}, NOW)
    store.close("XAUUSD", "win", NOW + 60)
    store.open("US30", {"side": "sell", "entry": 100.0, "sl": 101.0, "tp": 98.0, "bar_time": 2}, NOW)
    store.close("US30", "not_filled", NOW + 60)                        # not a result
    assert [(x["outcome"], x["R"]) for x in store.results] == [("win", 2.0)]


def test_supervisor_restarts_after_a_crash_and_stops_on_ctrl_c():
    calls, printed = [], io.StringIO()
    saved_run, saved_sleep = ict_alerts.run, ict_alerts.time.sleep
    sleeps = []

    def fake_run():
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("boom")
        raise KeyboardInterrupt

    ict_alerts.run, ict_alerts.time.sleep = fake_run, lambda s: sleeps.append(s)
    try:
        with contextlib.redirect_stdout(printed):
            try:
                ict_alerts.supervise()
            except KeyboardInterrupt:
                pass
    finally:
        ict_alerts.run, ict_alerts.time.sleep = saved_run, saved_sleep
    assert len(calls) == 2 and sleeps == [30] and "BOT CRASHED: ValueError: boom" in printed.getvalue()
    ict_alerts.run = lambda: (_ for _ in ()).throw(SystemExit("bad setting"))
    try:
        ict_alerts.supervise()                                          # bad settings stop it, no restart loop
        raise AssertionError("SystemExit expected")
    except SystemExit:
        pass
    finally:
        ict_alerts.run = saved_run


def late_retest_scenario():
    """The zone was retested 4 M5 candles before the confirmation: outside the strict window of 3."""
    d = scenario()
    m5 = [dict(b) for b in d["m5"]]
    hi = m5[-2]["close"]
    new = m5[:-1] + [bar(hi + .3, hi + .9, hi + .2, hi + .8), bar(hi + .8, hi + 1.0, hi + .5, hi + .6),
                     bar(hi + .6, hi + 2.0, hi + .5, hi + 1.8)]
    return dict(d, m5=retime(new, 300))


def test_active_profile_gives_more_signals_but_keeps_the_hard_rules():
    d = late_retest_scenario()
    strict, active = sl.make_config("strict"), sl.make_config("active")
    assert strict.min_confluence == 5 and active.min_confluence == 4 and active.retest_window == 5
    assert sl.evaluate("X", d, NOW, strict, spread=.2)["action"] == "WAIT"
    res = sl.evaluate("X", d, NOW, active, spread=.2)
    assert res["action"] == "BUY" and res["setup"]["profile"] == "active"
    assert "ACTIVE profile" in sl.format_signal("X", res) and "ملف نشط" in sl.format_signal_ar("X", res)
    # hard rules still apply under the active profile
    assert sl.evaluate("X", d, NOW - 250, active, spread=.2)["action"] == "WAIT"            # unclosed candle
    assert sl.evaluate("X", d, NOW, active, spread=None)["action"] == "WAIT"                # unknown cost
    assert sl.evaluate("X", dict(d, h4=scenario(h4_up=False)["h4"]), NOW, active, spread=.2)["action"] == "WAIT"
    assert sl.evaluate("X", d, NOW, active, spread=.2, status="STALE")["action"] == "WAIT"
    try:
        sl.make_config("wild")
        raise AssertionError("an unknown profile must be rejected")
    except ValueError:
        pass
    assert sl.make_config("strict").profile == "strict"


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("ok", name)
            except Exception as e:
                import traceback
                failed += 1
                print("FAIL", name, type(e).__name__, e)
                traceback.print_exc(limit=-2)
    raise SystemExit(1 if failed else 0)
