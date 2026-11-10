"""Dukascopy downloader with synthetic .bi5 files (no network). The prices are made up."""
import csv
import lzma
from datetime import datetime, timezone

import pytest

import backtest_runner as br
import fetch_dukascopy_history as fd

H0 = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)


def bi5(ticks):
    """ticks: [(ms, bid_price)] -> compressed file bytes as Dukascopy stores them (price x 1000)."""
    raw = b"".join(fd.TICK.pack(ms, int(round((p + .3) * 1000)), int(round(p * 1000)), 1.0, 1.0) for ms, p in ticks)
    return lzma.compress(raw, format=lzma.FORMAT_ALONE)


def test_parse_ticks_scales_the_bid_and_uses_ms_since_the_hour():
    ticks = fd.parse_ticks(bi5([(0, 2000.5), (1500, 2001.25)]), H0, 1000.0)
    base = H0.timestamp()
    assert ticks == [(base, 2000.5), (base + 1.5, 2001.25)]
    assert fd.parse_ticks(b"", H0, 1000.0) == []
    with pytest.raises(ValueError):
        fd.parse_ticks(b"not lzma", H0, 1000.0)


def test_to_m5_builds_ohlc_from_the_bid():
    base = int(H0.timestamp())
    c = fd.to_m5([(base + 10, 10.0), (base + 50, 12.0), (base + 200, 9.0), (base + 299, 11.0), (base + 300, 20.0)])
    assert c[base] == [10.0, 12.0, 9.0, 11.0] and c[base + 300] == [20.0] * 4


def test_the_url_uses_a_zero_based_month():
    urls = []

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b""

    fd.fetch_hour("XAUUSD", H0, opener=lambda req, timeout: (urls.append(req.full_url), R())[1])
    assert urls == ["https://datafeed.dukascopy.com/datafeed/XAUUSD/2024/00/15/10h_ticks.bi5"]


def test_download_skips_missing_hours_resumes_and_feeds_the_backtest_loader(tmp_path):
    files = {}
    for hour in range(10, 13):
        when = H0.replace(hour=hour)
        if hour != 11:                                            # 11h is missing, like a gap
            files[when] = bi5([(0, 2000 + hour), (60_000, 2001 + hour), (400_000, 2002 + hour)])
    fetch = lambda symbol, when: files.get(when, b"")           # noqa: E731
    out = str(tmp_path / "x.csv")
    now = H0.replace(hour=13)
    n = fd.download("XAUUSD", 1, out, fetch=fetch, now=now, sleep=lambda s: None, log=lambda m: None)
    rows = list(csv.reader(open(out)))
    assert n == len(rows) - 1 == 4 and rows[0] == ["time", "open", "high", "low", "close"]
    first = rows[1]
    assert first[1:] == ["2010.00000", "2011.00000", "2010.00000", "2011.00000"]
    files[H0.replace(hour=13)] = bi5([(0, 2013.0)])
    n2 = fd.download("XAUUSD", 1, out, fetch=fetch, now=H0.replace(hour=14), sleep=lambda s: None, log=lambda m: None)
    times = [int(r[0]) for r in list(csv.reader(open(out)))[1:]]
    assert n2 == 1 and times == sorted(set(times))
    bars, problems = br.load_csv(out)
    assert problems == [] and len(bars) == len(times)


def test_an_unknown_scale_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        fd.download("US30", 1, str(tmp_path / "y.csv"), fetch=lambda s, w: b"")


def http_error(code):
    import urllib.error
    return urllib.error.HTTPError("u", code, "Service Unavailable", {}, None)


def test_transient_errors_are_retried_with_waits():
    calls, waits = [], []

    def flaky(symbol, when):
        calls.append(1)
        if len(calls) == 1:
            raise http_error(503)
        if len(calls) == 2:
            raise TimeoutError("The read operation timed out")
        return b"ok"

    assert fd.fetch_with_retry(flaky, "XAUUSD", H0, sleep=waits.append, log=lambda m: None) == b"ok"
    assert len(calls) == 3 and waits == [3, 6]
    with pytest.raises(fd.DownloadStopped):
        fd.fetch_with_retry(lambda s, w: (_ for _ in ()).throw(http_error(503)), "X", H0, sleep=waits.append, log=lambda m: None)
    assert waits[2:] == [3, 6, 12, 24, 48, 60]            # capped at a minute, 7 tries


def test_a_persistent_failure_stops_without_leaving_a_gap_and_resumes(tmp_path):
    files = {H0.replace(hour=h): bi5([(0, 2000 + h), (60_000, 2001 + h)]) for h in (10, 11, 12)}
    broken = {H0.replace(hour=11)}

    def fetch(symbol, when):
        if when in broken:
            raise http_error(503)
        return files.get(when, b"")

    out = str(tmp_path / "g.csv")
    now = H0.replace(hour=13)
    with pytest.raises(fd.DownloadStopped) as e:
        fd.download("XAUUSD", 1, out, fetch=fetch, now=now, sleep=lambda s: None, log=lambda m: None)
    assert "11h" in str(e.value)
    saved = [int(r[0]) for r in list(csv.reader(open(out)))[1:]]
    assert saved and max(saved) < int(H0.replace(hour=11).timestamp())      # hour 12 was NOT written past the gap
    broken.clear()                                                          # the server recovers
    fd.download("XAUUSD", 1, out, fetch=fetch, now=now, sleep=lambda s: None, log=lambda m: None)
    times = [int(r[0]) for r in list(csv.reader(open(out)))[1:]]
    assert times == sorted(set(times)) and int(H0.replace(hour=12).timestamp()) in times
    assert int(H0.replace(hour=11).timestamp()) in times                    # no gap left


def test_skip_failed_continues_and_reports_the_gap(tmp_path):
    logs = []
    files = {H0.replace(hour=h): bi5([(0, 2000 + h)]) for h in (10, 12)}

    def fetch(symbol, when):
        if when.hour == 11:
            raise http_error(503)
        return files.get(when, b"")

    out = str(tmp_path / "s.csv")
    n = fd.download("XAUUSD", 1, out, fetch=fetch, now=H0.replace(hour=13), sleep=lambda s: None,
                    log=logs.append, skip_failed=True)
    assert n == 2 and any("SKIPPED" in m for m in logs)


def test_delay_and_tries_are_used_and_the_wait_is_capped():
    waits, calls = [], []

    def always_503(symbol, when):
        calls.append(1)
        raise http_error(503)

    with pytest.raises(fd.DownloadStopped):
        fd.fetch_with_retry(always_503, "XAUUSD", H0, tries=8, sleep=waits.append, log=lambda m: None)
    assert len(calls) == 8 and waits == [3, 6, 12, 24, 48, 60, 60]
    pauses = []
    files = {H0.replace(hour=10): bi5([(0, 2000.0)])}
    fd.download("XAUUSD", 1, "/dev/null", fetch=lambda s, w: files.get(w, b""), now=H0.replace(hour=12),
                sleep=pauses.append, log=lambda m: None, delay=1.5)
    assert len(pauses) == 24 and set(pauses) == {1.5}                       # one pause per hour of the day
