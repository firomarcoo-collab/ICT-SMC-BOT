"""fetch_binance_history with a fake Binance (no network): paging, resume, forming candle skipped."""
import csv

import backtest_runner as br
import fetch_binance_history as fh

STEP = 300_000
BASE = 1_700_000_100_000          # a multiple of STEP, like real aligned open times


def fake_binance(last_open):
    def fetch(symbol, interval, start_ms):
        rows, t = [], -(-start_ms // STEP) * STEP         # real klines are aligned to the interval
        while len(rows) < 1000 and t <= last_open:
            rows.append([t, "100", "101", "99", "100.5"])
            t += STEP
        return rows
    return fetch


def run(out, days, now_ms):
    return fh.download("BTCUSDT", "5m", days, out, fetch=fake_binance(BASE + 3000 * STEP), now_ms=now_ms,
                       sleep=lambda s: None, log=lambda m: None)


def test_download_pages_skips_forming_candle_and_resumes(tmp_path):
    out = str(tmp_path / "b.csv")
    now = BASE + 2100 * STEP + 1000                       # the candle opening at 2100 is still forming
    n = run(out, 2100 * STEP / 86_400_000, now)           # about 2100 candles: three pages
    rows = list(csv.reader(open(out)))
    assert rows[0] == ["time", "open", "high", "low", "close"] and n == len(rows) - 1 >= 2000
    assert int(rows[-1][0]) * 1000 == BASE + 2099 * STEP  # the forming candle is not saved
    n2 = run(out, 99, BASE + 2300 * STEP + 1000)          # resume later: only new candles are added
    times = [int(r[0]) for r in list(csv.reader(open(out)))[1:]]
    assert n2 == 200 and times == sorted(set(times)) and times[-1] * 1000 == BASE + 2299 * STEP
    bars, problems = br.load_csv(out)                     # the backtest runner reads what it wrote
    assert problems == [] and len(bars) == len(times)
