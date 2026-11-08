"""Offline checks for the US-open helpers and ai.py. Run: python test_ai_us.py"""
import io
import json
import urllib.error
from datetime import datetime, timezone

import ai
import extras
import ict_alerts
from ict_alerts import analyze
from ict_smc_bot import Config, to_series
from test_logic import make_candles


def ts(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


def test_us_open_window_winter():
    open_ts = ts(2026, 11, 11, 14, 30)  # 09:30 New York (EST)
    assert extras.us_open_ts(ts(2026, 11, 11, 12)) == open_ts
    assert extras.us_open_state(open_ts - 20 * 60) == -20
    assert extras.us_open_state(open_ts + 45 * 60) == 45
    assert extras.us_open_state(open_ts - 40 * 60) is None
    assert extras.us_open_state(open_ts + 3 * 3600) is None
    assert extras.us_open_ts(ts(2026, 11, 14, 15)) is None  # Saturday
    assert "opened 45 min ago" in extras.us_open_line(open_ts + 45 * 60)
    assert "opens in 20 min" in extras.us_open_line(open_ts - 20 * 60)
    assert extras.us_open_line(open_ts + 5 * 3600) == ""


def feed(candles):
    ict_alerts.get_candles = lambda inst, tf="5m": candles


def test_alert_mentions_us_open_and_filter():
    ict_alerts.INSTRUMENTS["TEST"] = {"source": "test", "id": "TEST", "digits": 2}
    candles = make_candles()
    feed(candles)
    now = int(candles[-1]["time"].timestamp()) + 8  # 2026-01-05 08:05 UTC: before the US open
    plain = analyze("TEST", Config(), now, set())[1]
    assert plain and "US OPEN window" not in plain[0]["text"]
    st, al = analyze("TEST", Config(), now, set(), None, {"us_open_only": True})
    assert al == [] and "US open" in st


def test_ai_context_only_on_entry_signals_when_enabled():
    candles = make_candles()
    feed(candles)
    now = int(candles[-1]["time"].timestamp()) + 8
    off = analyze("TEST", Config(), now, set())[1]
    assert all("context" not in a for a in off)
    on = analyze("TEST", Config(), now, set(), None, {"ai": True})[1]
    assert on and all("context" in a for a in on if a["kind"] != "event")
    assert "O" in on[0]["context"] and len(on[0]["context"].splitlines()) == 40


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ask_parses_response_and_sends_key_in_header():
    seen = {}

    def fake(req, timeout=0):
        seen["key"] = req.get_header("X-goog-api-key")
        seen["body"] = json.loads(req.data)
        return FakeResp(json.dumps({"candidates": [{"content": {"parts": [{"text": " ok "}]}}]}).encode())

    saved = ai.urllib.request.urlopen
    ai.urllib.request.urlopen = fake
    try:
        assert ai.ask("hi", "KEY", "m") == "ok"
    finally:
        ai.urllib.request.urlopen = saved
    assert seen["key"] == "KEY" and seen["body"]["contents"][0]["parts"][0]["text"] == "hi"


def test_ask_errors_are_readable():
    def boom(req, timeout=0):
        raise urllib.error.HTTPError("u", 429, "x", {}, io.BytesIO(b"quota"))

    saved = ai.urllib.request.urlopen
    ai.urllib.request.urlopen = boom
    try:
        try:
            ai.ask("hi", "K")
            assert False
        except RuntimeError as e:
            assert "429" in str(e) and "quota" in str(e) and "K" not in str(e).replace("quota", "")
    finally:
        ai.urllib.request.urlopen = saved


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
