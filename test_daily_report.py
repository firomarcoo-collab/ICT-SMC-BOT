"""Trades report: open / closed signals, floating and realised P/L in R, per day, Arabic text."""
import time

import daily_report as dr
import signal_store as ss


def make_store(tmp_path):
    return ss.SignalStore(str(tmp_path / "state.json"), log_path=str(tmp_path / "signals.log"))


def sig(store, market, side, entry, sl, tp, now, bar=1):
    ok, why = store.open(market, {"side": side, "entry": entry, "sl": sl, "tp": tp, "bar_time": bar}, now)
    assert ok, why


def test_open_trade_floating_r_for_buy_and_sell(tmp_path):
    s = make_store(tmp_path)
    now = int(time.time())
    sig(s, "XAUUSD", "buy", 100.0, 98.0, 106.0, now)
    sig(s, "BTCUSD", "sell", 50.0, 52.0, 44.0, now, bar=2)
    t = dr.build(s, now, {"XAUUSD": 101.0, "BTCUSD": 51.0})
    assert "المفتوحة الآن (2)" in t and "شراء" in t and "بيع" in t
    assert "عائم +0.50R" in t and "عائم -0.50R" in t and "مجموع العائم: +0.00R" in t
    assert dr.floating_r({"entry": 100, "sl": 98, "side": "buy"}, None) is None


def test_pending_limit_entries_are_marked_and_have_no_floating_pl(tmp_path):
    s = make_store(tmp_path)
    now = int(time.time())
    sig(s, "XAUUSD", "buy", 100.0, 98.0, 106.0, now)
    sid = next(iter(s.active_signals))
    t = dr.build(s, now, {"XAUUSD": 99.0}, {sid: "pending"})
    assert "بانتظار السعر للدخول" in t and "عائم" not in t


def test_closed_today_and_per_day_totals_in_r_and_money(tmp_path):
    s = make_store(tmp_path)
    now = int(time.time())
    sig(s, "XAUUSD", "buy", 100.0, 98.0, 106.0, now)
    s.close("XAUUSD", "win", now)                                   # +3R
    sig(s, "BTCUSD", "sell", 50.0, 52.0, 44.0, now, bar=2)
    s.close("BTCUSD", "loss", now)                                  # -1R
    s.results.append({"t": now - 86400, "outcome": "loss", "R": -1.0, "market": "EURUSD", "state": "SL_HIT"})
    t = dr.build(s, now, risk_usd=10.0)
    assert "المغلقة اليوم (2)" in t and "ربح +3.00R (≈ +30.00$)" in t and "خسارة -1.00R (≈ -10.00$)" in t
    assert "اليوم: 1 ربح، 1 خسارة، الصافي +2.00R (≈ +20.00$)" in t
    assert t.count("صفقة (") == 2 and "لا توجد صفقات مفتوحة" in t                 # two days listed
    assert "ليست حسابك الحقيقي" in t


def test_empty_state_and_money_hint(tmp_path, monkeypatch):
    monkeypatch.delenv("RISK_USD", raising=False)
    t = dr.build(make_store(tmp_path), int(time.time()))
    assert "لا توجد صفقات مفتوحة" in t and "لا توجد صفقات مغلقة اليوم" in t and "لا توجد نتائج مسجلة بعد" in t and "RISK_USD" in t
    monkeypatch.setenv("RISK_USD", "25")
    assert "RISK_USD" not in dr.build(make_store(tmp_path), int(time.time())) 


def test_notify_is_one_replaced_silent_notification_with_a_tap_action(monkeypatch):
    calls = []
    monkeypatch.setattr("shutil.which", lambda c: "/bin/" + c)
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **k: calls.append(cmd))
    dr.notify("تقرير")
    cmd = calls[0]
    assert cmd[cmd.index("--id") + 1] == "kenan-report" and cmd[cmd.index("--priority") + 1] == "low" and "--action" in cmd and "--sound" not in cmd
