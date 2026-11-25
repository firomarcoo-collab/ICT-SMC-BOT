import pytest

import unified_engine as ue
import session_engine

NOW = 1_800_000_000
SETUP = {"side": "buy", "entry": 2000.0, "sl": 1990.0, "tp1": 2010.0, "tp": 2020.0, "score100": 85, "bar_time": NOW - 600,
         "trends": {"h4": "up", "h1": "up", "m15": "up"}}
REPORT = {"liquidity": "SSL swept", "structure": "bullish CHoCH", "reversal": "displacement", "retest": "FVG retested", "confirm": "M15 close"}


def ev(name, direction="long", event="SIGNAL", entry=2001.0, proxy=False, zone=(1999.0, 2002.0), bar=NOW - 300):
    return {"name": name, "direction": direction, "event": event, "entry": entry, "sl": 1990.0, "tp1": 2010.0, "tp2": 2020.0, "rr": 2.0,
            "zone": zone, "proxy": proxy, "reason": "x", "bar_time": str(bar)}


SMC, SCALP, PULSE = "SMC Prime", "ICT Session Scalper", "Order Flow Pulse (estimate)"


def vote_all(h1="up", direction="long", **kw):
    for n in (SMC, SCALP, PULSE):
        ue.on_event("XAUUSD", ev(n, direction, **kw), h1, NOW - 120)


def test_all_three_plus_main_confirm_with_full_score():
    vote_all()
    ue.record_main("XAUUSD", SETUP, REPORT, NOW - 300, "sid1")
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "CONFIRMED BUY" and r["score"] == 100.0 and r["id"] == "XAUUSD-UNI-buy-sid1"


def test_one_module_alone_never_confirms():
    ue.on_event("XAUUSD", ev(SMC), "up", NOW - 60)
    ue.record_main("XAUUSD", SETUP, REPORT, NOW - 60, "sid1")
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "WAIT" and any("waiting for" in x for x in r["reasons"])


def test_main_chain_is_mandatory():
    vote_all()
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "WAIT" and "main SELECTIVE chain" in r["reasons"][0]


def test_opposite_module_is_a_veto_even_with_a_high_score():
    vote_all()
    ue.on_event("XAUUSD", ev(PULSE, "short"), "up", NOW - 30)           # Pulse flips to SELL
    ue.record_main("XAUUSD", SETUP, REPORT, NOW - 300, "sid1")
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "NO TRADE" and "conflict" in r["reasons"][0]


def test_invalidation_withdraws_the_vote():
    vote_all()
    ue.on_event("XAUUSD", ev(PULSE, "long", event="INVALIDATED"), "up", NOW - 20)
    ue.record_main("XAUUSD", SETUP, REPORT, NOW - 300, "sid1")
    assert ue.decide("XAUUSD", NOW)["modules"]["pulse"] == "WAIT"
    assert ue.decide("XAUUSD", NOW)["decision"] == "WAIT"


def test_votes_expire_and_stale_pulse_loses_a_check():
    vote_all()
    ue.record_main("XAUUSD", SETUP, REPORT, NOW - 300, "sid1")
    assert ue.decide("XAUUSD", NOW + ue.TTL["pulse"] + 10)["decision"] == "WAIT"          # Pulse expired
    old = NOW - 2400                                                                         # 40 min old Pulse: alive but not fresh
    ue.on_event("XAUUSD", ev(PULSE), "up", old)
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "CONFIRMED BUY" and 85 < r["score"] < 95


def test_score_below_80_waits_even_when_directions_agree():
    vote_all(h1="down", proxy=True, zone=None)                # many checks fail
    ue.record_main("XAUUSD", SETUP, REPORT, NOW - 300, "sid1")
    r = ue.decide("XAUUSD", NOW)
    assert r["decision"] == "WAIT" and r["score"] < 80 and "below the minimum" in r["reasons"][0]


def test_one_confirmation_per_main_signal_and_it_survives_a_restart():
    vote_all()
    ue.record_main("XAUUSD", SETUP, REPORT, NOW - 300, "sid1")
    first = ue.run_market("XAUUSD", NOW)
    assert first and first["text"].startswith("KENAN TRADER AI - CONFIRMED BUY") and "estimate" in first["text"]
    assert ue.run_market("XAUUSD", NOW + 60) is None                       # same signal: no repeat (state is read from the file)
    ue.record_main("XAUUSD", dict(SETUP, bar_time=NOW + 900), REPORT, NOW + 900, "sid2")
    assert ue.run_market("XAUUSD", NOW + 960) is not None                  # a NEW main signal may confirm again


@pytest.mark.parametrize("market", ["XAUUSD", "US100", "US30", "BTCUSD", "GBPUSD", "USDJPY", "EURUSD"])
def test_all_seven_markets_work_independently(market):
    for n in (SMC, SCALP, PULSE):
        ue.on_event(market, ev(n), "up", NOW - 100)
    ue.record_main(market, SETUP, REPORT, NOW - 100, "m1")
    assert ue.decide(market, NOW)["decision"] == "CONFIRMED BUY"
    other = "BTCUSD" if market != "BTCUSD" else "XAUUSD"
    assert ue.decide(other, NOW)["decision"] == "WAIT" if other not in ue._load()["votes"] else True
    assert session_engine.status(market, NOW)["mode"] != "unavailable"


def test_board_lists_modules_decision_and_active_count():
    vote_all()
    ue.record_main("XAUUSD", SETUP, REPORT, NOW - 300, "sid1")
    txt = ue.board(NOW)
    assert "SMC Prime: BUY" in txt and "Order Flow Pulse: BUY (estimate)" in txt and "Unified Decision: CONFIRMED BUY" in txt
    assert "Active confirmed signals: 1" in txt


def test_corrupt_state_file_never_crashes(tmp_path, monkeypatch):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    monkeypatch.setenv("UNIFIED_FILE", str(p))
    assert ue.decide("XAUUSD", NOW)["decision"] == "WAIT"
    assert ue.run_market("XAUUSD", NOW) is None


def test_quiet_module_alert_has_no_sound_or_vibration(monkeypatch):
    import alert_classes
    calls = []
    monkeypatch.setattr(alert_classes.shutil, "which", lambda c: c)
    monkeypatch.setattr(alert_classes.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    alert_classes.deliver("PULSE_SIGNAL", "x", "s1", quiet=True)
    assert len(calls) == 1 and "--sound" not in calls[0] and "--vibrate" not in calls[0] and "low" in calls[0]
    calls.clear()
    alert_classes.deliver("PULSE_SIGNAL", "x", "s2")
    assert "--sound" in calls[0] and any(c[0] == "termux-tts-speak" for c in calls)
