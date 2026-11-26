"""Arabic-only alerts: titles, explanation, tap-to-open full text, Arabic trade plan, Arabic reasons."""
import os

import alert_classes as ac
import decisions
import flip_ifvg as fl
import test_flip_ifvg as tf


def test_english_is_the_default_and_arabic_is_opt_in(monkeypatch):
    monkeypatch.delenv("ALERT_LANG", raising=False)
    assert ac.arabic() is False
    monkeypatch.setenv("ALERT_LANG", "ar")
    assert ac.arabic() is True


def test_every_alert_class_has_an_arabic_title_voice_and_explanation():
    for cls in list(ac.CLASSES) + list(ac.EXTRA_CLASSES):
        title, voice, expl = ac.AR[cls]
        assert title and voice and expl and not title.isascii() and not expl.isascii()


def test_deliver_uses_arabic_title_explanation_tap_action_and_buttons(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_LANG", "ar")
    calls = []
    monkeypatch.setattr(ac.shutil, "which", lambda c: "/bin/" + c)
    monkeypatch.setattr(ac.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    ac.deliver("STATE_FLIP", "نص التنبيه", "sid-1")
    notif = next(c for c in calls if c[0] == "termux-notification")
    assert "انقلاب الحالة" in notif[2] and "STATE FLIP" not in " ".join(notif)
    content = notif[notif.index("--content") + 1]
    assert "نص التنبيه" in content and "💡" in content
    act = notif[notif.index("--action") + 1]
    assert act.startswith("termux-open ")
    path = act.split(" ", 1)[1]
    assert "شرح" in open(path, encoding="utf-8").read()
    labels = [notif[i + 1] for i, x in enumerate(notif) if x in ("--button1", "--button2", "--button3")]
    assert labels == ["دخول", "انتظار", "تجاهل"]
    assert not any(c[0] == "termux-tts-speak" for c in calls)                    # the spoken voice is for SMC Prime only
    calls.clear()
    ac.deliver("SMC_SIGNAL", "x", "sid-2")
    tts = next(c for c in calls if c[0] == "termux-tts-speak")
    assert tts[1:3] == ["-l", "ar"]


def test_english_mode_keeps_the_english_titles(monkeypatch):
    calls = []
    monkeypatch.setattr(ac.shutil, "which", lambda c: "/bin/" + c)
    monkeypatch.setattr(ac.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    ac.deliver("STATE_FLIP", "x", "sid-1")
    notif = next(c for c in calls if c[0] == "termux-notification")
    assert "STATE FLIP" in notif[2] and "--action" in notif and "ENTER" in notif


def test_why_ar_translates_the_usual_gate_reasons_and_leaves_unknown_text():
    assert "السبريد غير معروف" in fl.why_ar("spread unknown (set SPREAD_<SYMBOL> or fill markets.json)")
    assert fl.why_ar("R:R 1:1.4 to the final target is under 1:2") == "العائد/المخاطرة 1:1.4 للهدف الأخير أقل من 1:2"
    assert "التقييم 65/100 أقل من 70" in fl.why_ar("score 65/100 is under 70")
    assert fl.why_ar("something else") == "something else"


def test_the_ifvg_flow_is_fully_arabic_in_ar(tmp_path, monkeypatch):
    monkeypatch.setattr(fl, "_targets", lambda *a, **k: tf.TARGETS)
    st = tf.fresh(tmp_path)
    b = tf.m15()
    first = tf.run(st, b, lang="ar")
    touch = tf.run(st, b, [tf.m5_bar(b, 99.4, 100.7, 99.3, 100.2)], lang="ar")
    conf = tf.m5_bar(b, 101.5, 101.7, 100.6, 100.9)
    signal = fl.process(st, "XAUUSD", b, [conf], "down", tf.now_after(b), "ar", 2, gate=lambda p: (True, ""))
    blocked_state = tf.fresh(tmp_path, "b.json")
    tf.run(blocked_state, b, lang="ar")
    tf.run(blocked_state, b, [tf.m5_bar(b, 99.4, 100.7, 99.3, 100.2)], lang="ar")
    blocked = fl.process(blocked_state, "XAUUSD", b, [conf], "down", tf.now_after(b), "ar", 2, gate=lambda p: (False, "spread unknown (set SPREAD_<SYMBOL> or fill markets.json)"))
    text = "\n".join(t for out in (first, touch, signal, blocked) for _, _, t in out)
    assert [c for c, _, _ in signal] == ["IFVG_SIGNAL"] and "الدخول الأمثل" in text and "وقف الخسارة الصلب" in text and "الخروج المبكر" in text
    assert "لم تصدر كإشارة: السبريد غير معروف" in text and "القرار لك" in text
    for bad in ("Optimal Entry", "Hard SL", "Early Exit", "Suggested", "NOT issued", "Current state", "Confluence: [", "Reason:", "Session:"):
        assert bad not in text, bad
