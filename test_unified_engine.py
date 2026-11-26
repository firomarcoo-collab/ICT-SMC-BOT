import pytest

import session_engine
import unified_engine as ue

NOW = 1_800_000_000
SETUP = {"side": "buy", "entry": 2000.0, "sl": 1990.0, "tp1": 2010.0, "tp": 2020.0, "score100": 85, "bar_time": NOW - 1800,
         "early_exit": 1995.0, "score_parts": {"displacement": 12}, "trends": {"h4": "up", "h1": "up", "m15": "up"}}
CHECKS = {"liquidity": True, "bos_mss": True, "fvg_ob": True, "retest": True, "confirm": True, "htf": True}
REPORT = {"liquidity": "SSL swept", "structure": "bullish CHoCH", "reversal": "displacement", "retest": "FVG retested", "confirm": "M15 close"}
SMC, SCALP, PULSE = "SMC Prime", "ICT Session Scalper", "Order Flow Pulse (estimate)"


def ev(name, direction="long", event="SIGNAL", entry=2001.0, proxy=False, zone=(1999.0, 2002.0), bar=NOW - 1500):
    return {"name": name, "direction": direction, "event": event, "entry": entry, "sl": 1990.0, "tp1": 2010.0, "tp2": 2020.0, "rr": 2.0,
            "zone": zone, "proxy": proxy, "reason": "x", "bar_time": str(bar)}


def vote_all(h1="up", direction="long", at=NOW - 120, **kw):
    for n in (SMC, SCALP, PULSE):
        ue.on_event("XAUUSD", ev(n, direction, **kw), h1, at)


def main(market="XAUUSD", checks=None, setup=None, sid="sid1", at=NOW - 300):
    return ue.record_main(market, setup or SETUP, REPORT, at, sid, CHECKS if checks is None else checks, "dec1")


# ---- the decision ---------------------------------------------------------------------------------------------------------------
def test_confirmed_example_has_full_score_and_one_id():
    vote_all(); main()
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "CONFIRMED BUY" and r["score"] == 100.0 and r["id"] == "XAUUSD-UNI-buy-sid1"
    assert r["levels"] == {"smc": "CONFIRMED", "scalper": "CONFIRMED", "pulse": "CONFIRMED"}


def test_no_trade_when_the_three_strategies_disagree_whatever_the_score():
    vote_all(); main()
    ue.on_event("XAUUSD", ev(PULSE, "short"), "up", NOW - 30)           # Pulse flips to SELL, everything else is perfect
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "NO TRADE" and "conflict" in r["reasons"][0] and r["score"] == 0.0
    assert ue.run_market("XAUUSD", NOW) is None


def test_no_trade_when_modules_disagree_even_without_the_main_chain():
    ue.on_event("XAUUSD", ev(SMC, "long"), "up", NOW - 100)
    ue.on_event("XAUUSD", ev(SCALP, "short"), "up", NOW - 100)
    assert ue.decide("XAUUSD", NOW)["decision"] == "NO TRADE"


@pytest.mark.parametrize("lost,label", [("liquidity", "liquidity sweep"), ("bos_mss", "CHoCH/MSS"), ("fvg_ob", "FVG/OB zone"),
                                        ("retest", "retest"), ("confirm", "closed M15 confirmation")])
def test_a_missing_mandatory_condition_blocks_even_with_a_perfect_score(lost, label):
    vote_all()
    main(checks=dict(CHECKS, **{lost: False}))
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "WAIT" and label in r["reasons"][0] and "mandatory" in r["reasons"][0]
    assert ue.run_market("XAUUSD", NOW) is None


def test_missing_displacement_blocks():
    vote_all()
    main(setup=dict(SETUP, score_parts={"displacement": 0}))
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "WAIT" and "displacement" in r["reasons"][0]


def test_hierarchy_levels_are_checked_in_order():
    main()
    ue.on_event("XAUUSD", ev(PULSE), "up", NOW - 100)                    # only Level 3 present
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "WAIT" and r["reasons"][0].startswith("Level 1")
    ue.on_event("XAUUSD", ev(SMC), "up", NOW - 100)
    assert ue.decide("XAUUSD", NOW)["reasons"][0].startswith("Level 2")
    ue.on_event("XAUUSD", ev(SCALP), "up", NOW - 100)
    assert ue.decide("XAUUSD", NOW)["decision"] == "CONFIRMED BUY"


def test_main_chain_is_mandatory():
    vote_all()
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "WAIT" and "main SELECTIVE chain" in r["reasons"][0]


def test_every_module_can_say_wait():
    vote_all(); main()
    ue.on_event("XAUUSD", ev(PULSE, event="REJECTED"), "up", NOW - 50)
    r = ue.decide("XAUUSD", NOW)
    assert r["modules"]["pulse"] == "WAIT" and r["decision"] == "WAIT" and "rejected" in r["reasons"][0]
    ue.on_event("XAUUSD", ev(SMC, event="MISSED"), "up", NOW - 40)
    assert ue.decide("XAUUSD", NOW)["modules"]["smc"] == "WAIT"


def test_invalidation_withdraws_the_vote_but_an_opposite_event_does_not():
    vote_all(); main()
    assert ue.on_event("XAUUSD", ev(PULSE, "short", event="INVALIDATED"), "up", NOW - 20) is False      # about the other direction
    assert ue.decide("XAUUSD", NOW)["modules"]["pulse"] == "BUY"
    ue.on_event("XAUUSD", ev(SCALP, "long", event="INVALIDATED"), "up", NOW - 20)
    assert ue.decide("XAUUSD", NOW)["modules"]["scalper"] == "WAIT"


# ---- closed candles and old signals -------------------------------------------------------------------------------------------------
def test_a_vote_on_an_unfinished_candle_is_refused():
    open_bar = NOW - 100                                                 # an M5 candle opened 100 s ago is not closed yet
    assert ue.on_event("XAUUSD", ev(SCALP, bar=open_bar), "up", NOW) is False
    assert ue.decide("XAUUSD", NOW)["modules"]["scalper"] == "WAIT"
    assert ue.on_event("XAUUSD", ev(SCALP, bar=NOW - 300), "up", NOW) is True


def test_a_main_signal_on_an_unfinished_m15_candle_is_refused():
    assert main(setup=dict(SETUP, bar_time=NOW - 300)) is False
    assert ue.decide("XAUUSD", NOW)["main"] is None


def test_old_signals_are_not_used():
    vote_all(); main()
    assert ue.decide("XAUUSD", NOW + ue.TTL["pulse"] + 10)["decision"] == "WAIT"        # Pulse expired
    assert ue.decide("XAUUSD", NOW + ue.MAIN_TTL + 10)["main"] is None                  # main expired


def test_a_lagging_feed_never_confirms():
    vote_all(); main()
    assert ue.run_market("XAUUSD", NOW, fresh=False) is None
    assert ue.run_market("XAUUSD", NOW, fresh=True) is not None          # the same signal confirms later when the data is fresh


def test_stale_pulse_loses_a_check_but_can_still_confirm():
    vote_all(); main()
    ue.on_event("XAUUSD", ev(PULSE, bar=NOW - 3000), "up", NOW - 2400)
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "CONFIRMED BUY" and 85 < r["score"] < 95


def test_score_below_80_waits_even_when_directions_agree():
    vote_all(h1="down", proxy=True, zone=None); main()
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "WAIT" and r["score"] < 80 and "below the minimum" in r["reasons"][0]


# ---- one alert, restart, validity -----------------------------------------------------------------------------------------------------
def test_one_confirmation_per_main_signal_and_it_survives_a_restart():
    vote_all(); main()
    first = ue.run_market("XAUUSD", NOW)
    assert first and first["text"].startswith("KENAN TRADER AI - CONFIRMED BUY") and "not real order flow" in first["text"]
    assert "Valid until" in first["text"] and "Cancelled if" in first["text"]
    assert ue.run_market("XAUUSD", NOW + 60) is None                       # same signal: no repeat (state is read from the file)
    main(setup=dict(SETUP, bar_time=NOW + 100), sid="sid2", at=NOW + 1100)
    assert ue.run_market("XAUUSD", NOW + 1200) is not None                 # a NEW main signal may confirm again


def test_confirmed_signal_expires():
    vote_all(); main()
    assert ue.run_market("XAUUSD", NOW)
    assert ue.check_active("XAUUSD", None, NOW + 60) == []
    out = ue.check_active("XAUUSD", None, NOW + 91 * 60)
    assert out and out[0][0] == "EXPIRED" and "Do not enter" in out[0][1]
    assert ue.check_active("XAUUSD", None, NOW + 92 * 60) == []             # reported once


def test_confirmed_signal_is_cancelled_by_a_full_close_beyond_the_invalidation():
    vote_all(); main()
    ue.run_market("XAUUSD", NOW)
    assert ue.check_active("XAUUSD", {"close": 1996.0}, NOW + 900) == []     # above 1995: still valid
    out = ue.check_active("XAUUSD", {"close": 1994.0}, NOW + 1800)
    assert out and out[0][0] == "CANCELLED" and "invalidation" in out[0][1]


def test_confirmed_signal_is_cancelled_when_a_module_turns_against_it():
    vote_all(); main()
    ue.run_market("XAUUSD", NOW)
    ue.on_event("XAUUSD", ev(SMC, "short"), "up", NOW + 600)
    out = ue.check_active("XAUUSD", {"close": 2005.0}, NOW + 900)
    assert out and out[0][0] == "CANCELLED" and "turned against" in out[0][1]


@pytest.mark.parametrize("market", ["XAUUSD", "US100", "US30", "BTCUSD", "GBPUSD", "USDJPY", "EURUSD"])
def test_all_seven_markets_work_independently(market):
    for n in (SMC, SCALP, PULSE):
        ue.on_event(market, ev(n), "up", NOW - 100)
    main(market)
    assert ue.decide(market, NOW)["decision"] == "CONFIRMED BUY"
    others = [m for m in ("XAUUSD", "US100", "US30", "BTCUSD", "GBPUSD", "USDJPY", "EURUSD") if m != market]
    assert all(ue.decide(o, NOW)["decision"] == "WAIT" for o in others)
    assert session_engine.status(market, NOW)["mode"] != "unavailable"


def test_board_lists_modules_decision_and_active_count():
    vote_all(); main()
    ue.run_market("XAUUSD", NOW)
    txt = ue.board(NOW)
    assert "SMC Prime: BUY" in txt and "Order Flow Pulse: BUY (candle-based estimate)" in txt and "Unified Decision: CONFIRMED BUY" in txt
    assert "Active confirmed signals: 1" in txt and "valid until" in txt


def test_corrupt_state_file_never_crashes(tmp_path, monkeypatch):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    monkeypatch.setenv("UNIFIED_FILE", str(p))
    assert ue.decide("XAUUSD", NOW)["decision"] == "WAIT"
    assert ue.run_market("XAUUSD", NOW) is None or True
    assert "UNIFIED BOARD" in ue.board(NOW)


def test_unified_can_be_switched_off(monkeypatch):
    assert ue.enabled()
    monkeypatch.setenv("UNIFIED", "0")
    assert not ue.enabled()


def test_quiet_module_alert_has_no_sound_or_vibration(monkeypatch):
    import alert_classes
    calls = []
    monkeypatch.setattr(alert_classes.shutil, "which", lambda c: c)
    monkeypatch.setattr(alert_classes.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    alert_classes.deliver("PULSE_SIGNAL", "x", "s1", quiet=True)
    assert len(calls) == 1 and "--sound" not in calls[0] and "--vibrate" not in calls[0] and "low" in calls[0]
    calls.clear()
    alert_classes.deliver("PULSE_SIGNAL", "x", "s2")
    assert "--sound" in calls[0] and not any(c[0] == "termux-tts-speak" for c in calls)      # voice is for SMC Prime only
    calls.clear()
    alert_classes.deliver("SMC_SIGNAL", "x", "s3", quiet=True)
    assert any(c[0] == "termux-tts-speak" for c in calls) and "--sound" not in calls[0]


# ---- Fibonacci: required, waived only on a strong confirmation ---------------------------------------------------------------------
def fib_votes(ok=(True, True, True), at=NOW - 120, h1="up"):
    for n, f in zip((SMC, SCALP, PULSE), ok):
        e = dict(ev(n), fib_ok=f, fib="Fib text")
        ue.on_event("XAUUSD", e, h1, at)


def test_fib_inside_on_all_modules_confirms(monkeypatch):
    monkeypatch.setenv("FIB_GATE", "1")
    fib_votes(); main()
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "CONFIRMED BUY" and "INSIDE" in r["fib"]


def test_fib_missing_is_waived_only_on_a_strong_confirmation(monkeypatch):
    monkeypatch.setenv("FIB_GATE", "1")
    fib_votes(ok=(True, False, None)); main(setup=dict(SETUP, score100=92))
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "CONFIRMED BUY" and r["fib"].startswith("WAIVED: strong confirmation")
    assert "Fibonacci: WAIVED" in ue.run_market("XAUUSD", NOW)["text"]


@pytest.mark.parametrize("what", ["h1_against", "main_score_low", "module_check_failed", "waiver_off"])
def test_fib_missing_without_a_strong_confirmation_waits(monkeypatch, what):
    monkeypatch.setenv("FIB_GATE", "1")
    setup = dict(SETUP, score100=92)
    if what == "h1_against":
        setup["trends"] = {"h4": "up", "h1": "down"}
    if what == "main_score_low":
        setup["score100"] = 85
    if what == "waiver_off":
        monkeypatch.setenv("FIB_WAIVER", "0")
    fib_votes(ok=(False, True, True))
    if what == "module_check_failed":
        fib_votes(ok=(False, True, True), h1="down")                     # SMC h1 check fails -> score < 100
    main(setup=setup)
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "WAIT" and "Fibonacci not confirmed" in r["reasons"][0]
    assert ue.run_market("XAUUSD", NOW) is None


def test_fib_switched_off_ignores_the_condition(monkeypatch):
    monkeypatch.setenv("FIB_GATE", "0")
    fib_votes(ok=(False, False, False)); main()
    assert ue.decide("XAUUSD", NOW)["decision"] == "CONFIRMED BUY"


def test_waiver_never_overrides_a_conflict_or_a_missing_mandatory_condition(monkeypatch):
    monkeypatch.setenv("FIB_GATE", "1")
    fib_votes(ok=(False, False, False)); main(setup=dict(SETUP, score100=95), checks=dict(CHECKS, retest=False))
    assert ue.decide("XAUUSD", NOW)["decision"] == "WAIT"
    ue.on_event("XAUUSD", ev(PULSE, "short"), "up", NOW - 10)
    assert ue.decide("XAUUSD", NOW)["decision"] == "NO TRADE"
