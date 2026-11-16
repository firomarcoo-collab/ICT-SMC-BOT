"""Robustness / safety tests added after the full audit. Run: python test_audit.py"""
import pytest
import contextlib
import io
import random
import time
from datetime import timedelta

import ict_alerts
import selective as sl
import signal_store
from test_selective import CFG, NOW, bar, retime, scenario, tmp_store

REAL_GET_CANDLES = ict_alerts.get_candles
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
    ict_alerts.get_candles = REAL_GET_CANDLES      # earlier tests replace it with a fake feed
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


def test_daily_risk_guard_stops_after_three_losses_in_a_row():
    store, _ = tmp_store()
    day = 1_800_000_000

    def finish(market, outcome, t):
        store.open(market, {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 102.0, "bar_time": t}, t)
        store.close(market, outcome, t + 60)

    assert store.halted(day) == ""
    finish("XAUUSD", "loss", day)
    assert store.halted(day + 200) == ""                               # one loss: still allowed
    finish("BTCUSD", "loss", day + 300)                                # the second, on another market
    assert store.halted(day + 400) == ""                               # two losses: still allowed
    finish("US30", "loss", day + 500)                                  # the third
    assert "3 losses in a row" in store.halted(day + 700)
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
    for market in ("XAUUSD", "BTCUSD", "US30"):
        store.open(market, {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 102.0, "bar_time": 1}, NOW - 900)
        store.close(market, "loss", NOW - 800)
    res = sl.process("NAS100", scenario(), NOW, store, CFG, spread=.2)
    assert res["action"] == "WAIT" and any("daily risk guard" in r for r in res["reasons"])
    off = sl.SelectiveConfig(max_daily_losses=0, max_daily_loss_r=0, ready_score=0)
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
    strict, active = sl.make_config("strict", ready_score=0), sl.make_config("active", ready_score=0)
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


def test_yahoo_fallback_symbol_is_sticky_labelled_and_never_mixed():
    import urllib.error
    rows = {"chart": {"result": [{"timestamp": [1700000000, 1700000300], "indicators": {"quote": [
        {"open": [1.0, 2.0], "high": [2.0, 3.0], "low": [0.5, 1.5], "close": [1.5, 2.5]}]}}]}}
    asked, saved = [], ict_alerts.fetch_json

    def fake(url):
        asked.append(url)
        if "XAUUSD%3DX" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return rows

    inst = {"source": "yahoo", "id": "XAUUSD=X", "digits": 2, "fallbacks": ["GC=F"]}
    ict_alerts.get_candles = REAL_GET_CANDLES      # earlier tests replaced it with a fake feed
    ict_alerts.fetch_json = fake
    ict_alerts._STICKY.clear()
    ict_alerts._CACHE.clear()
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            c = ict_alerts.get_candles(inst, "5m")
        assert len(c) == 2 and "GC%3DF" in asked[-1]
        assert "query1" in asked[0] and "query2" in asked[1]            # both hosts tried before the fallback
        assert "DATA SOURCE" in out.getvalue() and "GC=F" in ict_alerts.fallback_note(inst)
        asked.clear()
        ict_alerts.get_candles(inst, "15m")                              # sticky: no more tries of the broken one
        assert asked and all("GC%3DF" in u for u in asked)
        ict_alerts._STICKY["XAUUSD=X"] = ("GC=F", time.time() - 1)        # the fallback period ended
        asked.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            ict_alerts.get_candles(inst, "1h")
        assert "XAUUSD%3DX" in asked[0]                                  # the primary is tried again first
        # when everything fails the error is raised (the loop prints it and goes on)
        ict_alerts.fetch_json = lambda url: (_ for _ in ()).throw(RuntimeError("down"))
        ict_alerts._STICKY.clear()
        ict_alerts._CACHE.clear()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ict_alerts.get_candles(inst, "5m")
            raise AssertionError("an error was expected")
        except RuntimeError:
            pass
        assert ict_alerts.fallback_note(inst) == ""
    finally:
        ict_alerts.fetch_json = saved
        ict_alerts._STICKY.clear()
        ict_alerts._CACHE.clear()


def test_binance_stand_in_for_gold_when_yahoo_fails():
    import urllib.error
    assert ict_alerts.INSTRUMENTS["XAUUSD"]["fallbacks"][0] == "binance:PAXGUSDT"   # real-time stand-in first
    klines = [[1700000000000, "1.0", "2.0", "0.5", "1.5", "9", 1700000299999],
              [1700000300000, "1.5", "3.0", "1.0", "2.5", "9", 1700000599999]]
    asked, saved = [], ict_alerts.fetch_json

    def fake(url):
        asked.append(url)
        if "yahoo" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return klines

    inst = {"source": "yahoo", "id": "XAUUSD=X", "digits": 2, "fallbacks": ["binance:PAXGUSDT", "GC=F"]}
    ict_alerts.get_candles = REAL_GET_CANDLES
    ict_alerts.fetch_json = fake
    ict_alerts._STICKY.clear()
    ict_alerts._CACHE.clear()
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            c = ict_alerts.get_candles(inst, "5m")
        assert len(c) == 2 and c[-1]["close"] == 2.5
        assert "symbol=PAXGUSDT&interval=5m" in asked[-1] and not any("GC%3DF" in u for u in asked)
        assert "binance:PAXGUSDT" in out.getvalue() and "binance:PAXGUSDT" in ict_alerts.fallback_note(inst)
        ict_alerts.get_candles(inst, "4h")                                  # same stand-in for every timeframe
        assert "interval=4h" in asked[-1] and "PAXGUSDT" in asked[-1]
    finally:
        ict_alerts.fetch_json = saved
        ict_alerts._STICKY.clear()
        ict_alerts._CACHE.clear()


def test_open_trades_report():
    import os
    import tempfile
    import open_trades
    path = os.path.join(tempfile.mkdtemp(), "s.json")
    assert "No state file" in open_trades.describe(path)
    store = signal_store.SignalStore(path)
    now = int(time.time())
    store.open("XAUUSD", {"side": "buy", "entry": 4100.0, "sl": 4095.0, "tp": 4110.0, "bar_time": 1}, now)
    store.open("BTCUSD", {"side": "sell", "entry": 74000.0, "sl": 74300.0, "tp": 73400.0, "bar_time": 2}, now)
    store.close("BTCUSD", "loss", now)
    text = open_trades.describe(path)
    assert "XAUUSD: BUY entry 4100" in text and "BTCUSD" not in text.split("Today")[0]
    assert "1 finished, 0 wins, 1 losses, -1.0R" in text
    assert open_trades.describe(path).count("XAUUSD") == 1


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


def test_restart_follows_saved_signals_and_releases_the_lock(tmp_path):
    import tracker
    store = signal_store.SignalStore(str(tmp_path / "s.json"))
    ok, _ = store.open("XAUUSD", {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 102.0,
                                  "bar_time": 1000}, now_ts=1000)
    assert ok
    store2 = signal_store.SignalStore(str(tmp_path / "s.json"))       # new process
    track = tracker.Tracker(str(tmp_path / "r.csv"))
    unfollowed = ict_alerts.restore_tracking(store2, track, ["XAUUSD", "BTCUSD"])
    assert unfollowed == [] and len(track.items) == 1 and track.items[0]["state"] == "open"
    from datetime import datetime, timezone
    cs = [bar(100, 101, 99.5, 100.5), bar(100.5, 102.5, 100, 102)]    # newest first; r[0] is forming
    for i, c in enumerate(reversed(cs)):
        c["time"] = datetime.fromtimestamp(1300 + 300 * i, timezone.utc)
    forming = dict(cs[0], time=datetime.fromtimestamp(2000, timezone.utc))
    notes = track.update("XAUUSD", "5m", [forming] + list(reversed(cs)))
    assert any("WIN" in n for n in notes)
    assert store2.active("XAUUSD") is None                            # lock released after the restart
    assert store2.results[-1]["outcome"] == "win"


def test_restart_reports_locks_whose_feed_is_not_running(tmp_path):
    import tracker
    store = signal_store.SignalStore(str(tmp_path / "s.json"))
    store.open("BTCUSD", {"side": "sell", "entry": 100.0, "sl": 101.0, "tp": 98.0, "bar_time": 1}, now_ts=1)
    track = tracker.Tracker(str(tmp_path / "r.csv"))
    assert ict_alerts.restore_tracking(store, track, ["XAUUSD"]) == ["BTCUSD"]


def test_new_risk_defaults_match_the_research_settings():
    c = sl.SelectiveConfig()
    assert (c.risk_percent, c.max_daily_losses, c.max_daily_loss_pct) == (0.25, 3, 1.5)
    assert sl.daily_loss_limit_r(c) == 6.0                              # 1.5% / 0.25% per trade
    assert sl.daily_loss_limit_r(sl.SelectiveConfig(max_daily_loss_r=2.0)) == 2.0
    assert sl.daily_loss_limit_r(sl.SelectiveConfig(max_daily_loss_pct=0)) == 0.0
    assert (c.ready_score, c.watch_score) == (86, 75)


def parts(**kw):
    base = dict(htf_ok=True, mode="continuation", sweep_scope="external", mss=True, bos=False,
                zone_event=True, disp_ratio=1.6, zone_status="fresh", sr_hit=False, confirm=True,
                strong=True, cost_ok=True, net_rr=1.9)
    base.update(kw)
    return sl.score_breakdown(**base)


def test_score_out_of_100_follows_the_fixed_weights():
    p, total = parts()
    assert p == {"htf": 20, "liquidity": 15, "structure": 15, "displacement": 15, "zone": 15,
                 "confirm": 10, "execution": 10} and total == 100 == sum(sl.SCORE_MAX.values())
    assert parts(htf_ok=False, mode="reversal")[0]["htf"] == 10
    assert parts(htf_ok=False, mode="continuation")[0]["htf"] == 0
    assert parts(sweep_scope=None, sr_hit=True)[0]["liquidity"] == 4
    assert parts(sweep_scope=None)[0]["liquidity"] == 0
    assert parts(sweep_scope="swing")[0]["liquidity"] == 10
    assert parts(mss=False, bos=True)[0]["structure"] == 10
    assert parts(mss=False, bos=False, zone_event=False)[0]["structure"] == 0
    assert [parts(disp_ratio=r)[0]["displacement"] for r in (0.5, 0.8, 1.0, 1.5)] == [0, 8, 11, 15]
    assert parts(zone_status="partial")[0]["zone"] == 11
    assert parts(zone_status=None, sr_hit=True)[0]["zone"] == 6
    assert parts(strong=False)[0]["confirm"] == 6 and parts(confirm=False)[0]["confirm"] == 0
    assert parts(cost_ok=False)[0]["execution"] == 0
    assert parts(net_rr=1.6)[0]["execution"] == 7
    assert parts(cost_unverified=True)[0]["execution"] == 4            # an unverified spread caps it


def test_score_bands_and_that_only_ready_can_signal():
    c = sl.SelectiveConfig()
    assert [sl.score_band(x, c) for x in (100, 86, 85, 75, 74, 0)] == \
        ["READY", "READY", "WATCH", "WATCH", "REJECT", "REJECT"]
    d = scenario()
    res = sl.evaluate("X", d, NOW, c, spread=.2)                        # a continuation without a sweep
    assert res["action"] == "WAIT" and res["band"] in ("WATCH", "REJECT")
    assert any("score" in r and "/100" in r for r in res["reasons"])
    assert sum(res["score_parts"].values()) == res["score100"]
    low = sl.SelectiveConfig(ready_score=0)
    ok = sl.evaluate("X", d, NOW, low, spread=.2)
    assert ok["action"] == "BUY" and ok["setup"]["score100"] == ok["score100"]
    text = sl.format_signal("X", ok)
    assert f"Score {ok['score100']}/100" in text and "NOT a win probability" in text
    assert f"التقييم {ok['score100']}/100" in sl.format_signal_ar("X", ok)


def test_final_states_are_unified_and_still_accept_the_old_names(tmp_path):
    assert signal_store.TERMINAL_STATES == ("TP_HIT", "SL_HIT", "CANCELLED", "EXPIRED")
    assert [signal_store.final_state(x) for x in ("win", "loss", "not_filled", "manual", "expired")] == \
        ["TP_HIT", "SL_HIT", "CANCELLED", "CANCELLED", "EXPIRED"]
    assert signal_store.final_state("TP_HIT") == "TP_HIT"
    try:
        signal_store.final_state("maybe")
        raise AssertionError("an unknown outcome must be rejected")
    except ValueError:
        pass
    store = signal_store.SignalStore(str(tmp_path / "s.json"))
    sig = {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 102.0, "bar_time": 1}
    for market, outcome in (("XAUUSD", "TP_HIT"), ("BTCUSD", "SL_HIT"), ("US30", "CANCELLED"), ("NAS100", "EXPIRED")):
        assert store.open(market, dict(sig, bar_time=hash(market) % 1000), 10)[0]
        closed = store.close(market, outcome, 20)
        assert closed["state"] == outcome and store.active(market) is None
    assert [(x["state"], x["R"]) for x in store.results] == [("TP_HIT", 2.0), ("SL_HIT", -1.0)]
    assert [x["outcome"] for x in store.results] == ["win", "loss"]      # the risk guard keeps working
    rows = [__import__("json").loads(l) for l in open(store.log_path)]
    assert [r["state"] for r in rows if r["event"] == "CLOSE"] == ["TP_HIT", "SL_HIT", "CANCELLED", "EXPIRED"]
    store.open("XAUUSD", sig, 30)
    store.expire(30 + signal_store.DEFAULT_TTL + 1)                       # the TTL closes it as EXPIRED
    assert store.active("XAUUSD") is None
    rows = [__import__("json").loads(l) for l in open(store.log_path)]
    assert [r["state"] for r in rows if r["event"] == "CLOSE"][-1] == "EXPIRED"


def test_a_complete_reversal_with_the_trend_is_ready_and_a_counter_trend_one_is_only_watch():
    from test_ict_engine import reversal_scenario
    cfg = sl.SelectiveConfig()
    with_trend, against = (reversal_scenario("valid", h_up=up)[0] for up in (True, False))
    ready = sl.evaluate("X", with_trend, NOW, cfg, spread=.2)
    assert ready["action"] == "BUY" and ready["band"] == "READY" and ready["score100"] >= 86
    watch = sl.evaluate("X", against, NOW, cfg, spread=.2)
    assert watch["action"] == "WAIT" and watch["band"] == "WATCH" and watch["score_parts"]["htf"] == 10


def test_active_profile_accepts_80_to_85_with_a_reduced_confidence_label_but_strict_does_not():
    from test_ict_engine import reversal_scenario
    against = reversal_scenario("valid", h_up=False)[0]               # scores 83: WATCH under strict
    strict, active = sl.make_config("strict"), sl.make_config("active")
    assert (strict.ready_score, active.ready_score) == (86, 80)
    assert sl.evaluate("X", against, NOW, strict, spread=.2)["action"] == "WAIT"
    res = sl.evaluate("X", against, NOW, active, spread=.2)
    assert res["action"] == "BUY" and 80 <= res["score100"] < 86
    assert "REDUCED CONFIDENCE" in sl.format_signal("X", res) and "ثقة أقل" in sl.format_signal_ar("X", res)
    # the safety rules are the same under both profiles
    assert sl.evaluate("X", against, NOW, active, spread=None)["action"] == "WAIT"          # unknown cost
    assert sl.evaluate("X", against, NOW - 250, active, spread=.2)["action"] == "WAIT"      # unclosed candle
    assert sl.evaluate("X", against, NOW, active, spread=.2, status="STALE")["action"] == "WAIT"
    assert (active.max_daily_losses, active.max_daily_loss_pct, active.risk_percent) == (3, 1.5, 0.25)


def test_live_config_keeps_each_profile_defaults_unless_a_variable_is_set():
    """Regression: an unset READY_SCORE used to override the active profile's 80 with 86."""
    c = ict_alerts.build_selective_config({"PROFILE": "active", "MIN_NOTIFY_SCORE": "0"})
    assert (c.profile, c.ready_score, c.min_confluence) == ("active", 80, 4)
    assert ict_alerts.build_selective_config({}).ready_score == 86
    assert ict_alerts.build_selective_config({"PROFILE": "active", "READY_SCORE": "70"}).ready_score == 70
    assert ict_alerts.build_selective_config({"PROFILE": "strict", "MIN_CONFLUENCE": "4"}).min_confluence == 4
    risk = ict_alerts.build_selective_config({"PROFILE": "loose"})
    assert (risk.max_daily_losses, risk.max_daily_loss_pct, risk.risk_percent) == (3, 1.5, 0.25)
    assert ict_alerts.build_selective_config({"ALLOW_UNKNOWN_COST": "1"}).allow_unknown_cost is True
    for bad in ({"PROFILE": "wild"}, {"READY_SCORE": "abc"}):
        with pytest.raises(SystemExit):
            ict_alerts.build_selective_config(bad)


def test_loose_profile_is_the_most_permissive_and_changes_no_mandatory_rule():
    s, a, lo = (sl.make_config(p) for p in ("strict", "active", "loose"))
    assert (s.ready_score, a.ready_score, lo.ready_score) == (86, 80, 60)
    assert lo.strong_body_frac == 0.6 and s.strong_body_frac is None and a.strong_body_frac is None
    assert lo.min_confluence == 4          # 60% of 8 checks = 5, which is what strict already needs
    assert sl.PROFILES == ("strict", "active", "loose", "scalp", "swing", "golden", "forex")
    with pytest.raises(ValueError):
        sl.make_config("wild")


def test_strong_confirmation_by_body_share_of_range():
    def c(o, h, l, cl):
        return {"open": o, "high": h, "low": l, "close": cl}
    prev = c(100, 101, 99, 100.5)
    big_body = c(100.2, 100.9, 100.1, 100.8)          # bullish, closes below prev high (101): not strong by close
    assert sl.confirmation([prev, big_body], "buy") == (True, False, "")
    assert sl.confirmation([prev, big_body], "buy", 0.6) == (True, True, "")        # body 0.6 of 0.8 range
    wicky = c(100.2, 101.0, 99.8, 100.5)               # body 0.3 of 1.2 range
    assert sl.confirmation([prev, wicky], "buy", 0.6) == (True, False, "")
    assert sl.confirmation([prev, c(100, 100.2, 99.1, 99.2)], "sell", 0.6)[1] is True
    assert sl.confirmation([prev, c(100, 100, 100, 100)], "buy", 0.6)[0] is False   # doji is not bullish


def test_loose_profile_signals_a_setup_that_active_rejects_and_keeps_the_safety_rules():
    from test_ict_engine import reversal_scenario
    d = reversal_scenario("valid", h_up=False)[0]
    loose = sl.make_config("loose")
    res = sl.evaluate("X", d, NOW, loose, spread=.2)
    assert res["action"] == "BUY" and "LOOSE profile" in sl.format_signal("X", res)
    assert "ملف متساهل" in sl.format_signal_ar("X", res)
    assert sl.evaluate("X", d, NOW, loose, spread=None)["action"] == "WAIT"
    assert sl.evaluate("X", d, NOW - 250, loose, spread=.2)["action"] == "WAIT"
    assert sl.evaluate("X", d, NOW, loose, spread=.2, status="STALE")["action"] == "WAIT"


def test_startup_line_states_the_active_rules():
    line = ict_alerts.describe_config(ict_alerts.build_selective_config({"PROFILE": "loose", "MIN_NOTIFY_SCORE": "0",
                                                                       "ALLOW_UNKNOWN_COST": "1"}), "selective")
    assert "profile LOOSE" in line and "score 60+/100" in line and "4/8" in line
    assert "0.25%/trade" in line and "3 losses" in line and "1.5% a day" in line and "UNVERIFIED" in line
    strict = ict_alerts.describe_config(ict_alerts.build_selective_config({"ALLOW_UNKNOWN_COST": "0"}), "selective")
    assert "profile STRICT" in strict and "score 86+" in strict and "UNVERIFIED" not in strict
    default = ict_alerts.describe_config(ict_alerts.build_selective_config({}), "selective")
    assert "cost UNVERIFIED allowed" in default                      # unknown spread is labelled, not blocked
    assert ict_alerts.describe_config(None, "legacy") == "Engine: legacy"


def test_wait_line_marks_a_setup_that_only_missed_the_score_as_watch():
    from test_ict_engine import reversal_scenario
    against = reversal_scenario("valid", h_up=False)[0]               # scores 83 under strict
    res = sl.evaluate("X", against, NOW, sl.make_config("strict"), spread=.2)
    line = ict_alerts.wait_line("BTCUSD", res)
    assert line.startswith("BTCUSD: WATCH (score 83/100") and "needs 86" in line and "Parts" not in line
    plain = {"reasons": ["M15 structure is up, against a sell"], "report": {}}
    assert ict_alerts.wait_line("US30", plain) == "US30: SELECTIVE WAIT (M15 structure is up, against a sell)"
    assert ict_alerts.wait_line("US30", {"reasons": [], "report": {}}) == "US30: SELECTIVE WAIT (no setup)"


def _sig(side, bar, entry=100.0):
    return {"side": side, "entry": entry, "sl": entry - 1, "tp": entry + 2, "bar_time": bar}


def test_stacking_allows_more_signals_per_market():
    store = signal_store.SignalStore(None, max_active=2, allow_opposite=True)
    assert store.open("BTCUSD", _sig("buy", 1), 1000)[0]
    assert store.open("BTCUSD", _sig("sell", 2), 1000)[0]          # opposite side allowed
    ok, why = store.open("BTCUSD", _sig("buy", 3), 1000)           # third one: over the cap
    assert not ok and "blocked" in why
    store.close("BTCUSD-buy-1", "win", 1100)                        # closing by id frees one slot
    assert store.open("BTCUSD", _sig("buy", 3), 1200)[0]
    assert len(store.active_list("BTCUSD")) == 2


def test_stacking_default_and_no_opposite():
    store = signal_store.SignalStore(None)
    assert store.open("BTCUSD", _sig("buy", 1), 1000)[0]
    assert not store.open("BTCUSD", _sig("buy", 2), 1000)[0]        # default: one lock per market
    store2 = signal_store.SignalStore(None, max_active=3, allow_opposite=False)
    assert store2.open("BTCUSD", _sig("buy", 1), 1000)[0]
    assert store2.open("BTCUSD", _sig("buy", 2), 1000)[0]
    assert not store2.open("BTCUSD", _sig("sell", 3), 1000)[0]
    store2.close("BTCUSD", "loss", 1100)                            # a market name closes its oldest
    assert [s["bar_time"] for s in store2.active_list("BTCUSD")] == [2]


def test_old_state_file_keyed_by_market_still_loads(tmp_path):
    import json
    path = str(tmp_path / "s.json")
    old = {"id": "BTCUSD-buy-5", "market": "BTCUSD", "side": "buy", "entry": 1.0, "sl": 0.5,
           "tp": 2.0, "bar_time": 5, "created": 0, "state": "ACTIVE"}
    json.dump({"active": {"BTCUSD": old}, "seen": [], "used": [], "results": []}, open(path, "w"))
    store = signal_store.SignalStore(path)
    assert "BTCUSD-buy-5" in store.active_signals and store.active("BTCUSD")["id"] == "BTCUSD-buy-5"


def test_scalp_profile_has_near_target_and_labels():
    from selective import make_config, PROFILES
    assert "scalp" in PROFILES
    cfg = make_config("scalp")
    assert cfg.rr == 2.0 and cfg.min_net_rr == 1.5 and cfg.ready_score == 85 and cfg.max_risk_atr == 2.0
    assert make_config("strict").rr == 2.0
    import ict_alerts
    assert ict_alerts.build_selective_config({"PROFILE": "scalp"}).ready_score == 85


def _utc(y, mo, d, h, mi=0):
    import calendar
    return calendar.timegm((y, mo, d, h, mi, 0))


def test_us_cash_hours():
    import extras
    assert extras.us_cash_closed(_utc(2026, 11, 13, 15, 0)) == ""                  # Fri 10:00 New York
    assert extras.us_cash_closed(_utc(2026, 11, 13, 14, 30)) == ""                 # 09:30 sharp
    assert extras.us_cash_closed(_utc(2026, 11, 13, 21, 0)) != ""                  # 16:00 = closed
    assert extras.us_cash_closed(_utc(2026, 11, 13, 13, 0)) == "opens in 1h 30m"   # 08:00 New York
    assert extras.us_cash_closed(_utc(2026, 11, 13, 22, 0)) == "opens in 2d 16h"   # Fri 17:00 -> Mon 09:30
    assert extras.us_cash_closed(_utc(2026, 11, 14, 15, 0)).startswith("opens in")  # Saturday


def test_selective_scan_says_market_closed_for_us_indices_without_fetching(monkeypatch):
    import ict_alerts

    def boom(*a, **k):
        raise AssertionError("must not fetch candles while the market is closed")
    monkeypatch.setattr(ict_alerts, "get_candles", boom)
    monkeypatch.setitem(ict_alerts.INSTRUMENTS, "US30", dict(ict_alerts.INSTRUMENTS["US30"], id="^DJI"))
    scfg = ict_alerts.build_selective_config({})
    line, alert, lagging = ict_alerts.selective_scan("US30", _utc(2026, 11, 13, 22, 0), scfg, None)
    assert "MARKET CLOSED" in line and alert is None and lagging is False


def test_analyze_now_prints_closed_market_and_saves_nothing(monkeypatch):
    import analyze_now
    monkeypatch.setattr(analyze_now.time, "time", lambda: _utc(2026, 11, 14, 15, 0))   # Saturday
    import ict_alerts
    monkeypatch.setitem(ict_alerts.INSTRUMENTS, "US30", dict(ict_alerts.INSTRUMENTS["US30"], id="^DJI"))
    text = analyze_now.analyze("US30", {})
    assert "US30" in text and "MARKET CLOSED" in text and "No entry now" in text


def test_analyze_now_lifts_the_freshness_gate(monkeypatch):
    import analyze_now, ict_alerts
    seen = {}

    def fake_scan(name, now, scfg, store, *a, **k):
        seen["age"] = min(c.max_signal_age for c in (scfg if isinstance(scfg, list) else [scfg]))
        return f"{name}: SELECTIVE WAIT (x)", None, False
    monkeypatch.setattr(ict_alerts, "selective_scan", fake_scan)
    analyze_now.analyze("BTCUSD", {})
    assert seen["age"] > 10 ** 6


def test_scalp_bias_uses_m5_and_m1_and_keeps_the_both_htf_against_rule(monkeypatch):
    ev = lambda side: [{"side": side, "mss": False, "idx": 0}]      # noqa: E731
    monkeypatch.setattr(sl.st, "structure_events", lambda bars, k: bars)
    cfg = sl.make_config("active", ltf_bias=True)                   # the old M5 + M1 bias branch (still available)
    assert cfg.ltf_bias and not sl.make_config("active").ltf_bias
    ok, label = sl.htf_alignment(ev("bear"), ev("bear"), ev("bull"), "buy", sl.make_config("strict"), ev("bull"))
    assert not ok                                                   # strict: H4/H1 against -> blocked
    ok, label = sl.htf_alignment(ev("bull"), ev("bear"), ev("bear"), "buy", cfg, ev("bull"), ev("bull"))
    assert ok and "M5 up and M1 up" in label                        # M5 + M1 up; M15 down does not block
    ok, why = sl.htf_alignment(ev("bear"), ev("bear"), ev("bull"), "buy", cfg, ev("bull"), ev("bull"))
    assert not ok and "H4 and H1 are both" in why                   # both higher TFs against -> blocked
    ok, why = sl.htf_alignment(ev("bull"), ev("bull"), ev("bull"), "buy", cfg, ev("bear"), ev("bull"))
    assert not ok and "M5" in why                                   # M5 against -> blocked
    ok, why = sl.htf_alignment(ev("bull"), ev("bull"), ev("bull"), "buy", cfg, ev("bull"), ev("bear"))
    assert not ok and "M1 down" in why                              # M1 against -> blocked
    ok, label = sl.htf_alignment(ev("bull"), ev("bull"), ev("bull"), "buy", cfg, ev("bull"), None)
    assert ok and "M1 unavailable" in label                         # no M1 candles: M5 alone decides


def test_fib_format_shows_levels_ote_and_price_position():
    import analyze_now, structure
    pd = {"low": 100.0, "high": 110.0, "direction": "up", "zone": "discount", "eq": 105.0,
          "ote": (102.1, 103.8), "price": 103.0, "fib": structure.fib_levels(100.0, 110.0, "up")}
    text = analyze_now.fib_format(pd)
    assert "61.8%=103.82" in text and "INSIDE the OTE zone" in text and "1.618=116.18" in text
    pd["price"] = 108.0
    assert "above the OTE zone" in analyze_now.fib_format(pd)


def test_require_sweep_counter_trend_and_tf_agreement_options(monkeypatch):
    import ict_alerts
    assert sl.make_config("scalp").require_sweep and not sl.make_config("active").require_sweep
    assert ict_alerts.build_selective_config({"PROFILE": "scalp", "REQUIRE_SWEEP": "0"}).require_sweep is False
    assert ict_alerts.build_selective_config({"PROFILE": "active", "REQUIRE_SWEEP": "1"}).require_sweep is True
    ev = lambda side: [{"side": side, "mss": False, "idx": 0}]      # noqa: E731
    monkeypatch.setattr(sl.st, "structure_events", lambda bars, k: bars)
    cfg = sl.make_config("strict")
    line = sl.tf_agreement("buy", ev("bull"), ev("bull"), ev("bull"), ev("bear"), [], cfg)
    assert line == "M1 ✓ M5 ✓ M15 ✓ H1 ✗ H4 ?"


def test_calc_offset_line():
    import calc_offset
    text = calc_offset.offset_line("XAUUSD", 4012.35, 3884.10)
    assert "OFFSET_XAUUSD=128.25" in text


def test_wait_summary_counts_reasons_without_numbers():
    import ict_alerts as a
    k1 = a.reason_key("XAUUSD: SELECTIVE WAIT (scalp bias: M5 down, M1 up, need both up for a buy)")
    k2 = a.reason_key("US30: MARKET CLOSED (US session 09:30-16:00 New York, opens in 1h 30m)")
    assert "M# down" in k1 and k2 == "market closed"
    assert a.reason_key("X: SELECTIVE WAIT (stop too wide (3.4 M15 ATR))") == a.reason_key(
        "X: SELECTIVE WAIT (stop too wide (5.1 M15 ATR))")
    text = a.wait_summary({"XAUUSD": {"no retest": 7, "stop too wide": 2, "x": 1, "y": 1}}, 0)
    assert "0 signal(s)" in text and "XAUUSD (11 scans)" in text and "7x no retest" in text
    assert text.count("x ") <= 3


def test_swing_profile_far_target_wide_stop_and_labels():
    cfg = sl.make_config("swing")
    assert (cfg.rr, cfg.min_net_rr, cfg.max_risk_atr, cfg.ready_score, cfg.min_confluence) == (3.5, 3.0, 2.0, 80, 5)
    assert not cfg.ltf_bias and cfg.htf_mode == "d1h4" and cfg.struct_tf == "h1" and cfg.confirm_tf == "m15"
    import ict_alerts
    sw = ict_alerts.build_selective_config({"PROFILE": "swing"})
    assert sw.rr == 3.5 and sw.ready_score == 80 and sw.killzones == () and sw.blackout == ((20.0, 22.0),)


def test_several_profiles_first_that_passes_wins(monkeypatch):
    import ict_alerts
    monkeypatch.delenv("VERBOSE", raising=False)    # analyze_now (imported by other tests) sets it
    cfgs = ict_alerts.build_selective_configs({"PROFILE": "scalp,swing"})
    assert [c.profile for c in cfgs] == ["scalp", "swing"]
    assert [c.profile for c in ict_alerts.build_selective_configs({"PROFILE": "all"})] == [
        "strict", "swing", "golden", "active", "scalp", "forex"]
    assert [c.profile for c in ict_alerts.build_selective_configs({})] == ["strict"]
    assert "SCALP+SWING" in ict_alerts.describe_config(cfgs, "selective")
    seen = []

    def fake_process(market, data, now, store, cfg, *a, **k):
        seen.append(cfg.profile)
        return {"action": "WAIT", "reasons": [cfg.profile + " waits"], "report": {}, "checks": {}} \
            if cfg.profile == "scalp" else {"action": "WAIT", "reasons": ["x"], "report": {}, "checks": {}}
    monkeypatch.setattr(ict_alerts.selective, "process", fake_process)
    monkeypatch.setattr(ict_alerts, "get_candles", lambda inst, tf: [])
    monkeypatch.setattr(ict_alerts.selective, "data_status", lambda *a, **k: "LIVE")
    monkeypatch.setattr(ict_alerts.selective, "closed_only", lambda *a, **k: [])
    monkeypatch.setattr(ict_alerts.data_layer, "check_candles",
                        lambda *a, **k: type("Q", (), {"blocked": False, "reason": ""})())
    line, alert, _ = ict_alerts.selective_scan("BTCUSD", _utc(2026, 11, 14, 12, 0), cfgs, None)
    assert seen == ["scalp", "swing"] and alert is None and "scalp waits" in line


def test_fib_check_golden_zone(monkeypatch):
    up = {"low": 100.0, "high": 110.0, "direction": "up"}
    monkeypatch.setattr(sl.st, "premium_discount", lambda bars, k=3: dict(up))
    ok, text = sl.fib_check([], "buy", 103.0)           # 70% retracement of 100-110 (from the high)
    assert ok and "INSIDE" in text and "70%" in text
    ok, text = sl.fib_check([], "buy", 107.0)           # only 30%: too shallow
    assert ok is False and "outside" in text
    ok, text = sl.fib_check([], "sell", 103.0)          # a sell needs a down leg
    assert ok is False and "needs a down leg" in text
    monkeypatch.setattr(sl.st, "premium_discount", lambda bars, k=3: None)
    assert sl.fib_check([], "buy", 103.0)[0] is None


def test_golden_profile_and_env_override():
    import ict_alerts
    cfg = sl.make_config("golden")
    assert cfg.require_fib and cfg.ready_score == 75 and cfg.rr == 2.0 and not sl.make_config("active").require_fib
    assert ict_alerts.build_selective_configs({"PROFILE": "active", "REQUIRE_FIB": "1"})[0].require_fib


def test_us_session_open_close_events():
    import extras
    fri_closed, mon_open = _utc(2026, 11, 13, 22, 0), _utc(2026, 11, 16, 14, 30)      # Fri 17:00 NY, Mon 09:30 NY
    is_open, msg = extras.us_session_event(None, fri_closed, "US30")
    assert not is_open and "market closed" in msg and "opens" in msg                   # first look: status line
    assert extras.us_next_open_ts(fri_closed) == mon_open                                # the next open is Monday 09:30
    is_open, msg = extras.us_session_event(False, mon_open, "US30 / US100")
    assert is_open and msg.startswith("US MARKET OPEN")
    assert extras.us_session_event(True, mon_open + 300, "US30") == (True, None)        # no repeat while open
    is_open, msg = extras.us_session_event(True, _utc(2026, 11, 16, 21, 0), "US30")     # 16:00 NY
    assert not is_open and "session closed" in msg


def test_forex_symbols_and_pip_size():
    import markets, extras, ict_alerts
    assert markets.pip_size("EURUSD") == 0.0001 and markets.pip_size("gbpjpy") == 0.01
    assert markets.pip_size("JPYGBP") == 0.0001 and markets.pip_size("XAUUSD") is None
    for n in ("EURUSD", "GBPJPY", "JPYGBP"):
        assert n in ict_alerts.INSTRUMENTS and n in markets.load_markets() and n in extras.CURRENCIES
        assert not markets.load_markets()[n].verified()                 # nothing is assumed about the broker
    assert ict_alerts.INSTRUMENTS["GBPJPY"]["digits"] == 3
    assert markets.feed_name("GBPJPY") == "GBPJPY"


def test_forex_score_weights_bands_and_gates():
    parts, total = sl.forex_score(True, "continuation", "external", True, True, True,
                                  [{"kind": "FVG", "status": "fresh"}, {"kind": "OB", "status": "fresh"}],
                                  True, True, True, True, True)
    assert total == 100 == sum(sl.FOREX_SCORE_MAX.values()) and set(parts) == set(sl.FOREX_SCORE_MAX)
    assert all(parts[k] <= sl.FOREX_SCORE_MAX[k] for k in parts)
    assert [sl.forex_strength(x) for x in (69, 70, 79, 80, 89, 90, 100)] == [
        "NONE", "MODERATE", "MODERATE", "STRONG", "STRONG", "VERY STRONG", "VERY STRONG"]
    cfg = sl.make_config("forex")
    assert cfg.forex and cfg.single_active and cfg.ready_score == 70 and cfg.rr == 3.0
    assert sl.low_liquidity(_utc(2026, 11, 14, 12)) and sl.low_liquidity(_utc(2026, 11, 16, 22))
    assert sl.low_liquidity(_utc(2026, 11, 16, 10)) == "" and sl.low_liquidity(_utc(2026, 11, 15, 22))   # Sunday evening: still thin


def test_forex_m15_is_primary_and_m5_cannot_trigger_alone():
    import test_selective as t
    cfg = sl.make_config("forex", ready_score=0)
    res = sl.evaluate("EURUSD", t.scenario(), t.NOW, cfg, spread=.2)
    assert res["action"] == "BUY" and res["setup"]["tp1"] and res["setup"]["pip"] == 0.0001
    s = res["setup"]
    assert abs((s["tp1"] - s["entry"]) / (s["entry"] - s["sl"]) - 1.5) < 1e-9
    assert abs((s["tp"] - s["entry"]) / (s["entry"] - s["sl"]) - 3.0) < 1e-9
    assert "TP1" in sl.format_signal("EURUSD", res, 5) and "pips" in sl.format_signal("EURUSD", res, 5)
    # a stale M15 candle (the M5 one is fresh) must not produce a signal: M5 never triggers by itself
    late = sl.evaluate("EURUSD", t.scenario(), t.NOW + 600, cfg, spread=.2)
    assert late["action"] == "WAIT" and any("M15" in r for r in late["reasons"])


def test_forex_one_active_signal_per_symbol():
    import signal_store
    import test_selective as t
    cfg = sl.make_config("forex", ready_score=0)
    store = signal_store.SignalStore(None, max_active=3, allow_opposite=True)
    first = sl.process("EURUSD", t.scenario(), t.NOW, store, cfg, spread=.2)
    assert first["action"] == "BUY"
    second = sl.process("EURUSD", t.scenario(), t.NOW + 60, store, cfg, spread=.2)
    assert second["action"] == "WAIT"


def test_offsets_file_is_loaded_and_env_overrides_it(tmp_path, monkeypatch):
    import ict_alerts, calc_offset
    path = str(tmp_path / "offsets.json")
    monkeypatch.setattr(ict_alerts, "OFFSETS_FILE", path)
    assert ict_alerts.load_offsets({}) == {}
    data = calc_offset.save_offset("XAUUSD", 128.25, path)
    assert data == {"XAUUSD": 128.25}
    assert ict_alerts.load_offsets({}, ["XAUUSD", "BTCUSD"]) == {"XAUUSD": 128.25}
    assert ict_alerts.load_offsets({"OFFSET_XAUUSD": "5"}, ["XAUUSD"]) == {"XAUUSD": 5.0}
    assert calc_offset.save_offset("XAUUSD", 0, path) == {} and ict_alerts.load_offsets({}) == {}
    assert calc_offset.sane(4012.0, 3884.0) and not calc_offset.sane(40120.0, 3884.0)


def test_us_indices_use_24h_futures_by_default():
    import ict_alerts
    assert ict_alerts.INSTRUMENTS["US100"]["id"] == "NQ=F" and ict_alerts.INSTRUMENTS["US30"]["id"] == "YM=F"


def test_feed_env_overrides_the_primary_source(monkeypatch):
    import importlib
    import ict_alerts
    monkeypatch.setenv("FEED_XAUUSD", "binance:PAXGUSDT")
    try:
        mod = importlib.reload(ict_alerts)
        assert mod.INSTRUMENTS["XAUUSD"]["source"] == "binance" and mod.INSTRUMENTS["XAUUSD"]["id"] == "PAXGUSDT"
    finally:
        monkeypatch.delenv("FEED_XAUUSD")
        importlib.reload(ict_alerts)
    assert ict_alerts.INSTRUMENTS["XAUUSD"]["source"] == "yahoo"


def test_market_report_is_organised_one_line_per_market(monkeypatch):
    import report_tools, ict_alerts
    from datetime import datetime, timezone
    now = _utc(2026, 11, 16, 12, 0)

    def fake_candles(inst, tf):
        base = now - 100 * 60
        return [{"time": datetime.fromtimestamp(base - i * 300, tz=timezone.utc), "open": 1, "high": 2,
                 "low": 0.5, "close": 4000.0, "volume": 1} for i in range(60)]
    monkeypatch.setattr(ict_alerts, "get_candles", fake_candles)
    monkeypatch.setattr(report_tools, "direction_map",
                        lambda name, now: {"4h": "DOWN", "1h": "UP", "15m": "UP", "5m": "UP", "1m": "UP"})
    monkeypatch.setattr(report_tools, "dollar_state",
                        lambda now: {"price": 104.2, "h1": "UP", "m15": "UP", "change": 0.1})
    text = report_tools.market_report(["XAUUSD", "BTCUSD"], now, {"XAUUSD": {"no retest": 5}}, {"XAUUSD": 10.0}, 2)
    lines = text.splitlines()
    assert lines[1].startswith("USD index (DXY) 104.200")
    del lines[1]
    assert lines[0].startswith("===== MARKET REPORT 12:00 UTC") and "signals this hour: 2" in lines[0]
    assert lines[1].startswith("XAUUSD") and "4010.00" in lines[1] and "H4:DOWN" in lines[1]
    assert "M5+M1 UP = scalp bias" in lines[1] and "waits: no retest" in lines[1]
    assert lines[2].startswith("BTCUSD") and len(lines) == 3


def test_set_spec_validates_and_only_saves_what_you_type():
    import set_spec
    data = {"EURUSD": {"contract_size": None, "tick_size": None, "product_type": None, "broker_symbol": None}}
    out = set_spec.apply(data, "EURUSD", ["tick_size=0.00001", "contract_size=100000", "product_type=spot_cfd",
                                          "broker_symbol=EURUSD.m"])
    assert out["EURUSD"]["tick_size"] == 0.00001 and out["EURUSD"]["contract_size"] == 100000
    assert data["EURUSD"]["tick_size"] is None                      # the input is not modified
    for bad in (["tick_size=abc"], ["tick_size=0"], ["colour=red"], ["product_type=magic"], ["nokeyvalue"]):
        try:
            set_spec.apply(data, "EURUSD", bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    try:
        set_spec.apply(data, "NOPE", [])
    except ValueError:
        pass
    else:
        raise AssertionError("unknown market accepted")


def test_dollar_monitor_context_lines():
    import report_tools as rt
    usd = {"price": 104.2, "h1": "UP", "m15": "DOWN", "change": 0.31}
    assert "against this buy" in rt.dollar_signal_line("XAUUSD", "buy", usd)
    assert "supports this sell" in rt.dollar_signal_line("EURUSD", "sell", usd)
    assert rt.dollar_signal_line("US30", "buy", usd) == "" and rt.dollar_signal_line("XAUUSD", "buy", None) == ""
    assert "104.200" in rt.dollar_line(usd) and "no data" in rt.dollar_line(None)


# ===== tightened filtering =====

def test_tightened_defaults_in_live_config():
    import ict_alerts
    c = ict_alerts.build_selective_config({"PROFILE": "scalp"})
    assert c.ready_score >= 82 and c.watch_score == 75                  # phone alert 82+, 75-81 = WATCH
    assert c.min_net_rr >= 1.5 and c.rr >= 2.0 and c.spread_margin == 1.25
    assert c.require_mss_fvg and c.counter_trend_min_score == 90
    assert c.killzones == ((8.0, 11.0), (13.0, 16.5))               # scalp keeps its own sessions
    assert ict_alerts.build_selective_config({"PROFILE": "active"}).killzones == ((7.0, 11.0), (12.0, 16.0))
    off = ict_alerts.build_selective_config({"PROFILE": "active", "KILLZONES": "0", "REQUIRE_MSS_FVG": "0",
                                            "MIN_NOTIFY_SCORE": "75", "COUNTER_TREND_MIN_SCORE": "0"})
    assert off.killzones == () and not off.require_mss_fvg and off.ready_score == 80 and off.counter_trend_min_score == 0


def test_killzone_window_utc():
    import selective
    z = ((7.0, 11.0), (12.0, 16.0))
    day = 1_700_000_000 - 1_700_000_000 % 86400
    assert selective.in_killzone(day + 8 * 3600, z) and selective.in_killzone(day + 15 * 3600 + 1800, z)
    assert not selective.in_killzone(day + 3 * 3600, z)                 # Asian session
    assert not selective.in_killzone(day + 11 * 3600 + 1800, z) and not selective.in_killzone(day + 16 * 3600, z)
    assert selective.in_killzone(day + 3 * 3600, ())                    # no windows = always


def test_spread_margin_and_net_rr_floor():
    import selective
    cfg = selective.SelectiveConfig(rr=1.2, min_net_rr=1.5)
    assert not selective.cost_check(1.0, 0.0, 0.0, cfg)[0]              # 1:1.2 can never reach net 1.5
    ok = selective.SelectiveConfig(rr=2.0, min_net_rr=1.5, spread_margin=1.0)
    assert selective.cost_check(1.0, 0.1, 0.0, ok)[0]                   # net (2-0.1)/1.1 = 1.73
    wide = selective.SelectiveConfig(rr=2.0, min_net_rr=1.5, spread_margin=2.0, max_cost_frac=0.5)
    assert not selective.cost_check(1.0, 0.25, 0.0, wide)[0]            # margin doubles cost: (2-.5)/1.5 = 1.0


def test_killzone_blocks_evaluate_outside_hours():
    import selective
    cfg = selective.SelectiveConfig(killzones=((7.0, 11.0),))
    now = 1_700_000_000 - 1_700_000_000 % 86400 + 3 * 3600
    res = selective.evaluate("XAUUSD", {tf: [] for tf in ("h4", "h1", "m15", "m5")}, now, cfg)
    assert res["action"] == "WAIT"


def test_entry_checklist_in_both_languages():
    import selective
    s = {"side": "buy", "entry": 100.0, "sl": 99.0, "tp": 102.0, "cost": 0.1, "cost_note": "", "counter_trend": False}
    res = {"checks": {"confirm": True, "retest": True, "htf": True, "cost": True}}
    en = "\n".join(selective.entry_checklist(s, res))
    ar = "\n".join(selective.entry_checklist(dict(s, cost_note="COST UNVERIFIED"), res, ar=True))
    assert "Pre-entry checklist" in en and "OK spread verified" in en and "do not chase" in en
    assert "قائمة التحقق قبل الدخول" in ar and "✘ السبريد غير موثّق" in ar


# ===== the two modes: SCALP and SWING =====

def _bars(n, start=0, step=60, up=True, price=100.0):
    from datetime import datetime, timezone
    out, p = [], price
    for i in range(n):
        o, c = p, p + (0.5 if up else -0.5)
        out.append({"time": datetime.fromtimestamp(start + i * step, timezone.utc), "open": o, "close": c,
                    "high": max(o, c) + 0.1, "low": min(o, c) - 0.1})
        p = c
    return out


def test_mode_configs_scalp_and_swing():
    sc, sw = sl.make_config("scalp"), sl.make_config("swing")
    assert (sc.htf_mode, sc.ready_score, sc.min_net_rr, sc.m1_confirm) == ("h1m15", 85, 1.5, True)
    assert sc.killzones == ((8.0, 11.0), (13.0, 16.5)) and sc.strict_sequence and sc.min_displacement == 1.5
    assert (sw.htf_mode, sw.ready_score, sw.min_net_rr, sw.struct_tf, sw.confirm_tf) == ("d1h4", 80, 3.0, "h1", "m15")
    assert sw.strict_sequence and sw.late_entry_frac == 0.5 and sc.late_entry_frac == 0.5


def test_scalp_killzone_hours_london_ny_and_asia_muted():
    z = sl.make_config("scalp").killzones
    day = 1_700_000_000 - 1_700_000_000 % 86400
    ok = lambda h: sl.in_killzone(day + int(h * 3600), z)               # noqa: E731
    assert ok(8) and ok(10.9) and ok(13) and ok(16.4)
    assert not ok(3) and not ok(7.9) and not ok(11) and not ok(12.5) and not ok(16.5) and not ok(22)


def test_swing_blackout_last_two_hours_but_not_for_crypto():
    sw = sl.make_config("swing")
    day = 1_700_000_000 - 1_700_000_000 % 86400
    assert sl.in_killzone(day + 21 * 3600, sw.blackout) and not sl.in_killzone(day + 19 * 3600, sw.blackout)
    assert sl.is_crypto("BTCUSD") and not sl.is_crypto("XAUUSD")


def test_htf_modes_h1m15_and_d1h4(monkeypatch):
    ev = lambda side: [{"side": side, "mss": False, "idx": 0}]          # noqa: E731
    monkeypatch.setattr(sl.st, "structure_events", lambda bars, k: bars)
    sc, sw = sl.make_config("scalp"), sl.make_config("swing")
    ok, _ = sl.htf_alignment(ev("bear"), ev("bull"), ev("bull"), "buy", sc)          # H4 against does not matter for scalp
    assert ok
    ok, why = sl.htf_alignment(ev("bull"), ev("bull"), ev("bear"), "buy", sc)        # M15 against
    assert not ok and "M15" in why
    ok, _ = sl.htf_alignment(ev("bull"), ev("bear"), ev("bear"), "buy", sw, d1_trend="up")
    assert ok                                                                         # D1 + H4 up, H1/M15 ignored
    ok, why = sl.htf_alignment(ev("bull"), ev("bull"), ev("bull"), "buy", sw, d1_trend="down")
    assert not ok and "D1" in why
    ok, why = sl.htf_alignment(ev("bull"), ev("bull"), ev("bull"), "buy", sw, d1_trend=None)
    assert not ok                                                                     # no D1 data: swing waits


def test_m3_built_from_m1_and_confirmation():
    start = 1_700_000_000 - 1_700_000_000 % 180
    up = _bars(6, start, up=True)
    assert len(sl.m3_from_m1(up)) == 2 and sl.m3_from_m1(up)[0]["open"] == up[0]["open"]
    assert sl.m1_m3_agree(up, "buy")[0] and not sl.m1_m3_agree(up, "sell")[0]
    assert not sl.m1_m3_agree([], "buy")[0]                                            # no M1 data: no scalp entry
    mixed = _bars(5, start, up=True) + _bars(1, start + 300, up=False, price=102.5)
    assert not sl.m1_m3_agree(mixed, "buy")[0]


def test_late_entry_cancels_after_half_way_to_tp1():
    from datetime import datetime, timezone
    bar = lambda hi, lo: {"time": datetime.fromtimestamp(0, timezone.utc), "high": hi, "low": lo,   # noqa: E731
                          "open": lo, "close": hi}
    assert sl.late_entry([bar(100.6, 99.9)], "buy", 100.0, 101.0, 0.5)         # 60% of the way: cancelled
    assert not sl.late_entry([bar(100.4, 99.9)], "buy", 100.0, 101.0, 0.5)     # 40%: still fine
    assert sl.late_entry([bar(100.1, 99.4)], "sell", 100.0, 99.0, 0.5)
    assert not sl.late_entry([bar(100.6, 99.9)], "buy", 100.0, 101.0, 0)       # rule off


def test_scalp_waits_outside_killzone_and_swing_ignores_killzone():
    day = 1_700_000_000 - 1_700_000_000 % 86400
    empty = {tf: [] for tf in ("h4", "h1", "m15", "m5")}
    r = sl.evaluate("XAUUSD", empty, day + 3 * 3600, sl.make_config("scalp"))
    assert r["action"] == "WAIT"                                                  # not enough candles comes first
    import ict_alerts
    assert ict_alerts.build_selective_config({"PROFILE": "scalp", "KILLZONES": "0"}).killzones == ()
