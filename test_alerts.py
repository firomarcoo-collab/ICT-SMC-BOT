"""Offline checks for ict_alerts.py (no internet). Run: python test_alerts.py"""
import extras
import ict_alerts
from ict_alerts import INSTRUMENTS, analyze, parse_binance, parse_yahoo
from ict_smc_bot import Config
from test_classic import pullback_series
from test_logic import make_candles

INSTRUMENTS["TEST"] = {"source": "test", "id": "TEST", "digits": 2}
cfg = Config()


def forming_ts(candles):
    return int(candles[-1]["time"].timestamp())


def feed(candles):
    ict_alerts.get_candles = lambda inst, tf="5m": candles


def test_parse_binance():
    rows = [[1700000000000, "100.5", "101.0", "99.5", "100.8", "12", 1700000299999]]
    c = parse_binance(rows)[0]
    assert (c["open"], c["high"], c["low"], c["close"]) == (100.5, 101.0, 99.5, 100.8)
    assert int(c["time"].timestamp()) == 1700000000


def test_parse_yahoo_skips_empty_bars():
    data = {"chart": {"result": [{
        "timestamp": [1700000000, 1700000300, 1700000600],
        "indicators": {"quote": [{
            "open": [1.0, None, 3.0], "high": [2.0, None, 4.0],
            "low": [0.5, None, 2.5], "close": [1.5, None, 3.5]}]}}]}}
    out = parse_yahoo(data)
    assert len(out) == 2 and out[1]["close"] == 3.5


def test_alert_when_forming_bar_listed():
    candles = make_candles()
    feed(candles)
    status, alerts = analyze("TEST", cfg, forming_ts(candles) + 8, set())
    assert len(alerts) == 1 and alerts[0]["setup"]["side"] == "buy", (status, alerts)
    text = alerts[0]["text"]
    assert "BUY LIMIT TEST" in text and "Entry:" in text
    assert "Confluence: 1/4 (fib)" in text


def test_alert_when_forming_bar_missing():
    candles = make_candles()[:-1]  # the feed has not listed the forming bar yet
    feed(candles)
    now_ts = forming_ts(candles) + 300 + 8
    status, alerts = analyze("TEST", cfg, now_ts, set())
    assert len(alerts) == 1 and alerts[0]["setup"]["side"] == "buy", (status, alerts)


def test_same_signal_alerts_only_once():
    candles = make_candles()
    feed(candles)
    seen = set()
    now_ts = forming_ts(candles) + 8
    assert len(analyze("TEST", cfg, now_ts, seen)[1]) == 1
    assert analyze("TEST", cfg, now_ts, seen)[1] == []


def test_sell_alert():
    candles = make_candles(mirror=True)
    feed(candles)
    _, alerts = analyze("TEST", cfg, forming_ts(candles) + 8, set())
    assert len(alerts) == 1 and alerts[0]["setup"]["side"] == "sell"
    assert "SELL LIMIT" in alerts[0]["text"]


def test_min_score_filters_ict_alerts():
    candles = make_candles()  # this setup has 1 confirmation (fib)
    feed(candles)
    now_ts = forming_ts(candles) + 8
    assert len(analyze("TEST", Config(min_score=1), now_ts, set())[1]) == 1
    assert analyze("TEST", Config(min_score=2), now_ts, set())[1] == []


def test_classic_alert():
    candles = pullback_series()
    feed(candles)
    status, alerts = analyze("TEST", cfg, forming_ts(candles) + 8, set())
    classic = [a for a in alerts if a["kind"] == "classic"]
    assert len(classic) == 1 and classic[0]["setup"]["side"] == "buy", (status, alerts)
    assert "CLASSIC ALERT: BUY TEST" in classic[0]["text"]


def test_classic_can_be_turned_off():
    candles = pullback_series()
    feed(candles)
    _, alerts = analyze("TEST", Config(classic_enabled=False), forming_ts(candles) + 8, set())
    assert [a for a in alerts if a["kind"] == "classic"] == []


def test_offset_shifts_all_levels():
    candles = make_candles()
    feed(candles)
    now_ts = forming_ts(candles) + 8
    plain = analyze("TEST", cfg, now_ts, set())[1][0]
    shifted = analyze("TEST", cfg, now_ts, set(), {"TEST": 10.0})[1][0]
    for key in ("entry", "sl", "tp"):
        assert abs(shifted["setup"][key] - plain["setup"][key] - 10.0) < 1e-6, key
    assert "offset applied" in shifted["text"]


def test_custom_sound_is_played(monkeypatch=None):
    import os
    import tempfile
    calls = []
    saved = (ict_alerts.shutil.which, ict_alerts.subprocess.run, os.environ.get("ALERT_SOUND"))
    ict_alerts.shutil.which = lambda name: "/bin/" + name
    ict_alerts.subprocess.run = lambda cmd, **kw: calls.append(cmd)
    try:
        os.environ.pop("ALERT_SOUND", None)
        ict_alerts.notify("hello")                          # not an entry: no phone notification at all
        assert calls == []
        os.environ["INFO_NOTIFY"] = "1"
        ict_alerts.notify("hello")                          # INFO_NOTIFY=1: a silent notification
        assert not any(c[0] == "termux-media-player" for c in calls)
        assert "--sound" not in [c for c in calls if c[0] == "termux-notification"][0]
        calls.clear()
        ict_alerts.notify("hello", entry=True)
        assert any(c[0] == "termux-media-player" and c[1] == "play"
                   and c[2].endswith("alert.wav") for c in calls), calls
        note = [c for c in calls if c[0] == "termux-notification"][0]
        assert "--sound" not in note

        calls.clear()
        os.environ["ALERT_SOUND"] = "off"
        ict_alerts.notify("hello", entry=True)
        assert not any(c[0] == "termux-media-player" for c in calls)
        note = [c for c in calls if c[0] == "termux-notification"][0]
        assert "--sound" in note

        calls.clear()
        with tempfile.NamedTemporaryFile(suffix=".mp3") as f:
            os.environ["ALERT_SOUND"] = f.name
            ict_alerts.notify("hello")                      # silent even with a custom file
            assert not any(c[0] == "termux-media-player" for c in calls)
            ict_alerts.notify("hello", entry=True)
            assert [c for c in calls if c[0] == "termux-media-player"][0][2] == f.name
    finally:
        ict_alerts.shutil.which, ict_alerts.subprocess.run = saved[0], saved[1]
        os.environ.pop("INFO_NOTIFY", None)
        if saved[2] is None:
            os.environ.pop("ALERT_SOUND", None)
        else:
            os.environ["ALERT_SOUND"] = saved[2]


def test_only_entry_alerts_may_make_a_sound():
    import inspect
    src = inspect.getsource(ict_alerts.run)
    assert 'notify(alert["text"], entry=True, side=alert["setup"]["side"])' in src                       # selective entry
    assert 'entry=alert["kind"] in ENTRY_KINDS' in src                      # legacy: ict / classic / rct only
    assert "event" not in ict_alerts.ENTRY_KINDS
    for call in ("notify(f\"US OPEN", "notify(\"US MARKET", "notify(f\"SESSION", "notify(f\"NEWS", "notify(note, alert="):
        line = [ln for ln in src.splitlines() if call in ln][0]
        assert "entry=True" not in line, call                               # info messages stay silent


def test_buy_is_blue_and_sell_is_red():
    import io
    import contextlib
    calls = []
    saved = (ict_alerts.shutil.which, ict_alerts.subprocess.run)
    ict_alerts.shutil.which = lambda name: "/bin/" + name
    ict_alerts.subprocess.run = lambda cmd, **kw: calls.append(cmd)
    try:
        for side, emoji, ansi, led in (("buy", "\U0001F535", "\033[1;94m", "0066FF"),
                                       ("sell", "\U0001F534", "\033[1;91m", "FF0000")):
            calls.clear()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ict_alerts.notify("SIGNAL", entry=True, side=side)
            assert out.getvalue().startswith(ansi) and "\033[0m" in out.getvalue()      # coloured terminal text
            note = [c for c in calls if c[0] == "termux-notification"][0]
            assert note[note.index("--title") + 1].startswith(emoji)                    # coloured circle
            assert note[note.index("--content") + 1].startswith(emoji + " SIGNAL")
            assert note[note.index("--led-color") + 1] == led                           # notification light
        calls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            ict_alerts.notify("NEWS soon")                                              # info: screen only
        assert calls == []
    finally:
        ict_alerts.shutil.which, ict_alerts.subprocess.run = saved


def test_extras_in_alert_text():
    candles = make_candles()
    feed(candles)
    now_ts = forming_ts(candles) + 8
    opts = {"balance": 1000.0, "contracts": {"TEST": 1.0}}
    text = analyze("TEST", cfg, now_ts, set(), None, opts)[1][0]["text"]
    assert "Session:" in text and "Lot:" in text and "$1000" in text, text
    plain = analyze("TEST", cfg, now_ts, set())[1][0]["text"]
    assert "Lot:" not in plain


def test_session_only_and_news_block():
    candles = make_candles()
    feed(candles)
    now_ts = forming_ts(candles) + 8
    ev = [{"ts": now_ts + 600, "title": "NFP", "currency": "USD"}]
    warn = analyze("TEST", cfg, now_ts, set(), None, {"news": ev})[1][0]["text"]
    assert "WARNING high-impact news: USD NFP in 10 min" in warn
    st, al = analyze("TEST", cfg, now_ts, set(), None, {"news": ev, "news_block": True})
    assert al == [] and "news" in st
    saved = extras.active_sessions
    extras.active_sessions = lambda ts: []
    try:
        st, al = analyze("TEST", cfg, now_ts, set(), None, {"session_only": True})
    finally:
        extras.active_sessions = saved
    assert al == [] and "sessions" in st


def test_entry_decision_ignores_the_forming_bar_price():
    for forming_close in (102.9, 100.5, 120.0, 90.0):
        candles = make_candles()
        candles[-1] = dict(candles[-1], close=forming_close, high=max(103.0, forming_close),
                           low=min(102.5, forming_close))
        feed(candles)
        _, alerts = analyze("TEST", cfg, forming_ts(candles) + 8, set())
        assert [a["setup"]["side"] for a in alerts if a["kind"] == "ict"] == ["buy"], forming_close


def test_event_alerts_only_when_enabled():
    candles = make_candles()
    candles[-4] = dict(candles[-4], open=100.1, high=100.2, close=100.1)  # a clear gap above it
    feed(candles)
    now_ts = forming_ts(candles) + 8
    base = analyze("TEST", cfg, now_ts, set())[1]
    assert [a for a in base if a["kind"] == "event"] == []
    alerts = analyze("TEST", cfg, now_ts, set(), None, {"events": ("sweep", "fvg", "ob")})[1]
    kinds = {a["text"].split(" ALERT")[0] for a in alerts if a["kind"] == "event"}
    assert kinds >= {"FVG", "OB"}, kinds


def test_stale_data_means_market_closed():
    candles = make_candles()
    feed(candles)
    status, alerts = analyze("TEST", cfg, forming_ts(candles) + 3600, set())
    assert alerts == [] and "market closed" in status


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)


def test_management_alerts_show_a_notification_but_never_play_the_entry_tone():
    import contextlib
    import io
    calls = []
    saved = (ict_alerts.shutil.which, ict_alerts.subprocess.run)
    ict_alerts.shutil.which = lambda name: "/bin/" + name
    ict_alerts.subprocess.run = lambda cmd, **kw: calls.append(cmd)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            ict_alerts.notify("REVERSAL WARNING: move the stop", alert=True)
            managed = list(calls)
            calls.clear()
            ict_alerts.notify("info only")                                  # a plain info message: nothing
            plain = list(calls)
    finally:
        ict_alerts.shutil.which, ict_alerts.subprocess.run = saved
    assert [c[0] for c in managed] == ["termux-notification"]              # no termux-media-player
    assert "--sound" not in managed[0] and managed[0][managed[0].index("--priority") + 1] == "high"
    assert managed[0][managed[0].index("--title") + 1] == "ICT SMC (manage)"
    assert plain == []
