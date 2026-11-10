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
