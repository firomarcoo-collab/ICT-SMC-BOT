from datetime import timedelta

import displacement_alerts as da
import displacement_engine as de
from test_displacement_engine import CONFIRM, RETEST, TARGET, at, htf_bars, long_expansion


def setup_module(_):
    da._ENGINES.clear()


def test_replays_silently_and_announces_only_fresh_bars():
    da._ENGINES.clear()
    htf = htf_bars()
    ltf = long_expansion()
    out = da.process("XAUUSD", ltf, htf, 2)
    assert [c for c, _, _ in out] == ["DISP_SETUP"] and "SETUP ONLY" in out[0][2]
    assert da.process("XAUUSD", ltf, htf, 2) == []                           # same bar: nothing
    ltf = ltf + [RETEST(1)]
    assert [c for c, _, _ in da.process("XAUUSD", ltf, htf, 2)] == ["DISP_RETEST"]
    ltf = ltf + [CONFIRM(2)]
    out = da.process("XAUUSD", ltf, htf, 2)
    assert [c for c, _, _ in out] == ["DISP_SIGNAL"]
    t = out[0][2]
    assert "CONFIRMED ENTRY" in t and "Hard SL" in t and "TP2" in t and "Early invalidation" in t and "Signal only" in t


def test_restart_does_not_repeat_old_events():
    da._ENGINES.clear()
    htf = htf_bars()
    ltf = long_expansion() + [RETEST(1), CONFIRM(2)]
    da.process("XAUUSD", ltf[:-2], htf)           # earlier scan
    da._ENGINES.clear()                           # restart
    later = ltf + [{"time": TARGET + timedelta(minutes=15 * 3), "open": 112.8, "high": 112.9, "low": 112.7, "close": 112.8, "volume": 100}]
    assert da.process("XAUUSD", later, htf) == []   # the confirmation happened 1 bar ago but was replayed, then only fresh events count


def test_no_volume_feed_relaxes_the_filter_and_says_so():
    da._ENGINES.clear()
    nov = [dict(b, volume=0) for b in long_expansion()]
    out = da.process("EURUSD", nov, htf_bars(), 5)
    assert [c for c, _, _ in out] == ["DISP_SETUP"] and "No volume data" in out[0][2]


def test_switch_off(monkeypatch):
    monkeypatch.setenv("DISPLACEMENT", "0")
    assert not da.enabled("XAUUSD")


def test_classes_exist_for_the_phone():
    import alert_classes
    for c in ("DISP_SETUP", "DISP_RETEST", "DISP_SIGNAL", "DISP_INVALIDATED"):
        assert c in alert_classes.EXTRA_CLASSES
    assert de.Config().killzones
