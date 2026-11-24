import signal_manager as sm
import telegram_alerts as ta


class Store:
    def __init__(self, rows): self.rows = rows
    def active_list(self, m): return self.rows.get(m, [])


def test_claim_blocks_same_market_and_allows_others(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_MANAGER_FILE", str(tmp_path / "sm.json"))
    assert sm.allow("XAUUSD", "buy", 2000, 1000, "displacement")[0]
    ok, why = sm.allow("XAUUSD", "sell", 2010, 1100, "scalper")
    assert not ok and "one active signal per market" in why
    assert sm.allow("US30", "buy", 40000, 1100, "scalper")[0]          # markets are independent


def test_claim_survives_restart_and_expires(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_MANAGER_FILE", str(tmp_path / "sm.json"))
    sm.allow("BTCUSD", "buy", 1, 1000, "pulse")
    assert not sm.allow("BTCUSD", "buy", 1, 1500, "smc")[0]            # re-read from disk
    assert sm.allow("BTCUSD", "buy", 1, 1000 + 7 * 3600, "smc")[0]     # TTL passed


def test_release_and_store_block(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_MANAGER_FILE", str(tmp_path / "sm.json"))
    sm.allow("USDJPY", "sell", 1, 1000, "pulse")
    sm.release("USDJPY")
    assert sm.allow("USDJPY", "sell", 1, 1001, "pulse")[0]
    ok, why = sm.allow("GBPUSD", "buy", 1, 1, "scalper", store=Store({"GBPUSD": [{"side": "buy", "id": "x1"}]}))
    assert not ok and "x1" in why


def test_single_instance_lock(tmp_path):
    p = str(tmp_path / "bot.lock")
    first = sm.single_instance(p)
    assert first
    assert sm.single_instance(p) is None


def test_telegram_layout_and_classes(monkeypatch):
    setup = {"side": "buy", "entry": 2000.0, "sl": 1990.0, "tp1": 2010.0, "tp": 2020.0, "bar_time": 1700000000,
             "score100": 85, "profile": "strict", "trends": {"h4": "bull", "h1": "bull", "m15": "bull"}}
    t = ta.format_signal("XAUUSD", setup, {"report": {}}, "London", "clear", 2, "0.3")
    assert t.startswith("KENAN TRADER AI") and "Quality Score: 85/100" in t and "1:2.0" in t and "Liquidity Sweep: -" in t
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    assert not ta.send("x") and not ta.send_class("ZONE_WATCH", "x")
    sent = []
    monkeypatch.setenv("TELEGRAM_TOKEN", "t"); monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(ta, "send", lambda text, silent=False: sent.append((text, silent)) or True)
    ta.send_class("ZONE_WATCH", "hello"); ta.send_class("IFVG_SIGNAL", "go")
    assert "NOT an entry signal" in sent[0][0] and sent[0][1] and sent[1] == ("go", False)


def test_review_extended_metrics():
    import review
    rows = [{"R": "2.0", "mfe_r": "2.3", "symbol": "XAUUSD"}, {"R": "-1.0", "mfe_r": "0.2", "symbol": "XAUUSD"},
            {"R": "-1.0", "mfe_r": "0.0", "symbol": "US30"}, {"R": "1.0", "mfe_r": "1.1", "symbol": "US30"}]
    e = review.extended(rows)
    assert e["tp1_rate"] == 50 and e["tp2_rate"] == 25 and e["sl_rate"] == 50 and e["max_losing_streak"] == 2
    assert abs(e["expectancy"] - 0.25) < 1e-9
    out = review.build(rows, by=("symbol", "score"), lang="en")
    assert "too early" in out and "unknown" in out and "expectancy" in out


def test_signal_class_notification_has_sound_and_prep_does_not(monkeypatch):
    import alert_classes
    calls = []
    monkeypatch.setattr(alert_classes.shutil, "which", lambda c: c)
    monkeypatch.setattr(alert_classes.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    monkeypatch.setenv("ALERT_VOICE", "0")
    alert_classes.deliver("IFVG_SIGNAL", "x", "s1")
    alert_classes.deliver("ZONE_APPROACH", "y", "s2")
    assert "--sound" in calls[0] and "--sound" not in calls[1]
