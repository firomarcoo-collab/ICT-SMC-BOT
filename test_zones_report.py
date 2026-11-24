import zones_report as zr


def obs(side="buy", stage3=True, zone=True, rel="in"):
    return {"side": side, "stage3": stage3, "rel": rel if zone else None,
            "zone": {"kind": "FVG", "low": 100.0, "high": 101.0} if zone else None}


def test_grade_strong_medium_weak():
    assert zr.grade(obs(), "up")[0] == "STRONG"
    assert zr.grade(obs(), "down")[0] == "STRONG"          # 4 of 5 with the mandatory checks
    assert zr.grade(obs(rel="far"), "down")[0] == "MEDIUM"
    assert zr.grade(obs(stage3=False, rel="far"), "down")[0] == "WEAK"
    g, score, missing = zr.grade(obs(zone=False), None)
    assert g == "WEAK" and "fresh FVG/OB zone" in missing


def test_sell_needs_down_trend():
    assert "H1 agrees" in zr.grade(obs("sell"), "up")[2]
    assert "H1 agrees" not in zr.grade(obs("sell"), "down")[2]


def test_build_en_and_ar_and_empty():
    zr.LATEST.clear()
    assert "No ready zones" in zr.build(1000, lang="en")
    zr.record("XAUUSD", [obs()], 100.5, 1000, "up")
    t = zr.build(1000, lang="en")
    assert "XAUUSD BUY" in t and "STRONG (5/5)" in t and "not a win probability" in t
    assert "قوية" in zr.build(1000, lang="ar")
    assert "No ready zones" in zr.build(1000 + 99999, lang="en")      # stale data is hidden
    zr.LATEST.clear()


def test_gbpusd_usdjpy_are_wired():
    import ict_alerts
    import markets
    assert markets.pip_size("GBPUSD") == 0.0001 and markets.pip_size("USDJPY") == 0.01
    assert ict_alerts.INSTRUMENTS["GBPUSD"]["id"] == "GBPUSD=X" and ict_alerts.INSTRUMENTS["USDJPY"]["digits"] == 3
    assert "forex" in ict_alerts.strategies_for("GBPUSD") and "forex" in ict_alerts.strategies_for("USDJPY")
    assert markets.canonical("usdjpy") == "USDJPY"


def test_empty_report_never_notifies(monkeypatch):
    calls = []
    monkeypatch.setattr("shutil.which", lambda c: "/bin/" + c)
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **k: calls.append(cmd))
    zr.notify("📍 Entry zones near price (information only, not a signal)\nNo ready zones right now.")
    assert calls == []
    zr.LATEST.clear()
    zr.record("XAUUSD", [obs()], 100.5, 1000, "up")
    zr.notify(zr.build(1000, lang="en"))
    assert calls and "--alert-once" in calls[0] and calls[0][0] == "termux-notification"
    zr.LATEST.clear()
