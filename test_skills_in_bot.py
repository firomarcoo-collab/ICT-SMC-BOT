import doctor
import displacement_alerts as da
import review
from test_displacement_engine import CONFIRM, RETEST, htf_bars, long_expansion


def rows(n, r):
    return [{"R": str(r), "symbol": "XAUUSD", "profile": "selective", "session": "London", "mode": "x", "side": "buy"} for _ in range(n)]


def test_review_too_early_and_verdicts():
    t = review.build(rows(5, 1.0), lang="en")
    assert "too early (5/30)" in t and "No profit is guaranteed" in t
    assert "positive so far" in review.build(rows(20, 1.0) + rows(20, -0.5), lang="en")
    assert "losing" in review.build(rows(20, -1.0) + rows(15, 0.5), lang="en")
    assert "لا توجد صفقات" in review.build([], lang="ar") and "No closed trades" in review.build([], lang="en")


def test_doctor_finds_overrides_and_missing_tools():
    res = doctor.check({"SYMBOLS": "BTCUSD,XAUUSD", "TWELVEDATA_API_KEY": "abc"}, {"~/.bashrc": "export ALERT_LANG=ar\n# SYMBOLS=old\n"},
                       {"termux-notification": False}, "abc123 msg")
    text = " ".join(m for _, m in res)
    assert "missing" in text and "GBPUSD" in text and "ALERT_LANG=ar" in text and "# SYMBOLS" not in text
    assert ("BAD", "termux-notification missing: install the Termux:API app and run: pkg install termux-api") in res
    assert "abc" not in text.replace("abc123", "")                          # the key is never printed
    ok = doctor.check({}, {}, {"termux-notification": True, "termux-tts-speak": True})
    assert all(l != "BAD" for l, _ in ok) and any("defaults are used" in m for _, m in ok)


def test_lot_line_asks_for_balance_then_sizes(monkeypatch):
    da._ENGINES.clear()
    monkeypatch.delenv("BALANCE", raising=False)
    htf, ltf = htf_bars(), long_expansion() + [RETEST(1), CONFIRM(2)]
    out = []
    for i in (0, 1, 2):
        out += da.process("XAUUSD", ltf[:len(ltf) - 2 + i], htf, 2)
    sig = [t for c, _, t in out if c == "DISP_SIGNAL"][0]
    assert "set BALANCE" in sig
    ev = {"entry": 112.8, "sl": 111.5, "risk_scale": 1.0}
    monkeypatch.setenv("BALANCE", "1000")
    assert da.lot_line("XAUUSD", ev).startswith("- Lot:")
    assert "Lot" in da.lot_line("NOPE", ev)
