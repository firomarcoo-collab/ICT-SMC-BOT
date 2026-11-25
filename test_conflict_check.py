import conflict_check as cc

EV = {"name": "Order Flow Pulse", "direction": "long", "entry": 58850.0, "sl": 58709.55, "tp1": 59005.33, "tp2": 59160.66}
LATEST = {"BTCUSD": {"ts": 1000, "obs": [{"side": "sell", "zone": {"kind": "FVG", "low": 58938.0, "high": 59014.0}}]}}


def test_btc_case_is_flagged():
    out = cc.conflicts("BTCUSD", EV, "down", LATEST, 1100)
    assert any("against the H1 trend" in c for c in out)
    assert any("opposing SELL zone 58938 - 59014" in c for c in out)
    assert any("ESTIMATE" in c for c in out)
    assert cc.warning_text("BTCUSD", EV, "down", LATEST, 1100).startswith("⛔ CONFLICT")


def test_clean_signal_has_no_conflict_and_estimate_is_only_a_note():
    ev = dict(EV, name="SMC Prime")
    assert cc.warning_text("BTCUSD", ev, "up", {}, 1100) == ""
    note = cc.warning_text("BTCUSD", EV, "up", {}, 1100)
    assert note.startswith("ℹ️ Note") and "CONFLICT" not in note


def test_zone_off_the_path_or_stale_is_ignored_and_short_mirror():
    far = {"BTCUSD": {"ts": 1000, "obs": [{"side": "sell", "zone": {"kind": "FVG", "low": 60000.0, "high": 60100.0}}]}}
    assert not [c for c in cc.conflicts("BTCUSD", dict(EV, name="x"), "up", far, 1100) if "zone" in c]
    assert not cc.conflicts("BTCUSD", dict(EV, name="x"), "up", LATEST, 1000 + 99999)          # stale zones report
    short = dict(EV, name="x", direction="short", entry=59100.0, tp1=58900.0)
    buy_zone = {"BTCUSD": {"ts": 1000, "obs": [{"side": "buy", "zone": {"kind": "OB", "low": 58950.0, "high": 59000.0}}]}}
    assert any("opposing BUY zone" in c for c in cc.conflicts("BTCUSD", short, "up", buy_zone, 1100))
    assert any("against the H1 trend" in c for c in cc.conflicts("BTCUSD", short, "up", {}, 1100))
