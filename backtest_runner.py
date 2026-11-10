"""Backtest runner for the selective engine: real CSV candles in, a documented report out.

It never fabricates data. Without a CSV file it does nothing. Feed it M5 candles exported from the
broker (MT5: View > Symbols > Bars > Export, or any CSV with time,open,high,low,close). M15 / H1 /
H4 / D1 are built from the M5 file, so one file per market is enough.

What it does differently from the quick `backtest_selective.replay`:
  * goes through selective.process(), so the market lock, the consumed liquidity events and zones
    (no re-use after a loss) and the daily risk guard behave exactly as they do live;
  * shows the engine the same window the live bot sees (the last 300 closed candles per timeframe);
  * charges spread, commission and slippage on every trade; a candle that touches both stop and
    target counts as a LOSS (conservative);
  * reports per market, continuation vs reversal, score band, an in-sample / out-of-sample split,
    consecutive time windows (walk-forward style stability, no parameter is tuned) and a count of
    why signals were blocked, so "why so few signals" is answered from data instead of guessed.

Broker time: MT5 server time is usually UTC+2 / UTC+3, not UTC. The sessions and the daily levels
in the engine use UTC, so pass --tz-offset (hours the broker clock is AHEAD of UTC) or the sessions
will be wrong.

Usage:
    python backtest_runner.py --market XAUUSD --m5 XAUUSD_M5.csv --spread 0.26 --tz-offset 3 \\
        [--d1 XAUUSD_D1.csv] [--profile strict] [--commission 0] [--slippage 0] [--out report.json]

A result is only a historical measurement. A sample under 200 trades per market is too small to
conclude anything, and a good past result does not promise future results.
"""
import argparse
import bisect
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import selective as sl
from signal_store import SignalStore

WINDOW = 300          # candles per timeframe shown to the engine, same as the live bot
MIN_TRADES = 200      # minimum sample per market before the numbers mean anything
TF_MINUTES = {"m5": 5, "m15": 15, "h1": 60, "h4": 240, "d1": 1440}
_FIELDS = ("time", "open", "high", "low", "close")


# ===== Loading =====

def _parse_time(text, tz_hours=0.0):
    t = text.strip()
    if re.fullmatch(r"\d{9,13}(\.\d+)?", t):                     # epoch seconds or milliseconds
        v = float(t)
        v = v / 1000.0 if v > 1e11 else v
        return datetime.fromtimestamp(v, timezone.utc)
    t = t.replace("T", " ").replace("Z", "")
    if re.match(r"\d{4}\.\d\d\.\d\d", t):                         # MT5 style 2024.01.02
        t = t.replace(".", "-", 2)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return (datetime.strptime(t, fmt) - timedelta(hours=tz_hours)).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise ValueError(f"unreadable time {text!r}")


def load_csv(path, tz_hours=0.0):
    """Read candles from a CSV. Returns (bars oldest first, problems list).
    Accepted layouts (header required): time,open,high,low,close[,volume] with ISO / MT5 style times
    or epoch seconds/milliseconds; or the MT5 export <DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,...
    (comma, tab or semicolon separated). Epoch times are UTC; text times are broker time minus
    `tz_hours`. Bad rows are skipped and listed; duplicates keep the last row; bars come back sorted."""
    with open(path, newline="") as f:
        text = f.read()
    sample = text[:2000]
    delim = max(",\t;", key=sample.count)
    rows = list(csv.reader(text.splitlines(), delimiter=delim))
    if not rows:
        return [], ["the file is empty"]
    head = [re.sub(r"[<>\s]", "", h).lower() for h in rows[0]]
    if "date" in head and "time" in head:                         # MT5 export: separate date and time
        di, ti = head.index("date"), head.index("time")
        idx = {k: head.index(k) for k in _FIELDS[1:] if k in head}
        tcol = lambda r: f"{r[di]} {r[ti]}"                      # noqa: E731
    else:
        if not set(_FIELDS) <= set(head):
            raise ValueError(f"the header must contain {', '.join(_FIELDS)}; found {head}")
        idx = {k: head.index(k) for k in _FIELDS}
        tcol = lambda r: r[idx["time"]]                          # noqa: E731
    if not set(_FIELDS[1:]) <= set(idx):
        raise ValueError("the file needs open, high, low and close columns")
    out, problems = {}, []
    for n, r in enumerate(rows[1:], 2):
        if not any(x.strip() for x in r):
            continue
        try:
            t = _parse_time(tcol(r), tz_hours)
            o, h, l, c = (float(r[idx[k]]) for k in ("open", "high", "low", "close"))
        except (ValueError, IndexError) as e:
            problems.append(f"row {n}: {e}")
            continue
        if not (h >= max(o, c, l) and l <= min(o, c, h)) or min(o, h, l, c) <= 0:
            problems.append(f"row {n}: inconsistent OHLC")
            continue
        out[t] = {"time": t, "open": o, "high": h, "low": l, "close": c}
    return [out[k] for k in sorted(out)], problems


def resample(bars, minutes):
    """Aggregate M5 (or finer) bars into `minutes` candles aligned to UTC. Only COMPLETE candles are
    kept: a group needs every sub-bar present, so a gap (weekend, holiday) never makes a short candle."""
    base = (bars[1]["time"] - bars[0]["time"]).total_seconds() / 60 if len(bars) > 1 else 5
    need = int(round(minutes / base))
    groups = {}
    for b in bars:
        ts = int(b["time"].timestamp())
        groups.setdefault(ts - ts % (minutes * 60), []).append(b)
    out = []
    for start in sorted(groups):
        g = groups[start]
        if len(g) < need:
            continue
        out.append({"time": datetime.fromtimestamp(start, timezone.utc), "open": g[0]["open"],
                    "high": max(x["high"] for x in g), "low": min(x["low"] for x in g),
                    "close": g[-1]["close"]})
    return out


def build_data(m5, d1=None):
    """All timeframes from the M5 bars; D1 from its own file when given (else built from M5)."""
    data = {tf: resample(m5, mins) for tf, mins in TF_MINUTES.items() if tf not in ("m5", "d1")}
    data["m5"] = m5
    data["d1"] = d1 if d1 is not None else resample(m5, 1440)
    return data


# ===== Replay =====

class _Series:
    """Closed candles of one timeframe, with the last `WINDOW` before a moment found by bisection."""

    def __init__(self, bars, minutes):
        self.bars, self.sec = bars, minutes * 60
        self.ends = [int(b["time"].timestamp()) + self.sec for b in bars]

    def at(self, now_ts):
        hi = bisect.bisect_right(self.ends, now_ts)
        return self.bars[max(0, hi - WINDOW):hi]


def simulate(setup, future, cost):
    """Walk the candles after the signal. Stop is checked first (both touched = loss).
    Returns (result, R_net, bars_used) or None when the data ends first."""
    buy = setup["side"] == "buy"
    risk = abs(setup["entry"] - setup["sl"])
    reward = abs(setup["tp"] - setup["entry"])
    for n, c in enumerate(future, 1):
        stop = c["low"] <= setup["sl"] if buy else c["high"] >= setup["sl"]
        target = c["high"] >= setup["tp"] if buy else c["low"] <= setup["tp"]
        if stop:
            return "loss", -(risk + cost) / risk, n
        if target:
            return "win", (reward - cost) / risk, n
    return None


def replay(market, data, cfg=None, spread=None, commission=None, start=120, progress=None):
    """Bar-by-bar replay. Returns {'trades': [...], 'blocked': Counter, 'evaluated': n, 'signals': n}.
    `commission` is a price amount per round trip in the instrument's price units, like `spread`."""
    cfg = cfg or sl.SelectiveConfig()
    cfg = sl.SelectiveConfig(**{**cfg.__dict__, "allow_historical": True})
    series = {tf: _Series(data[tf], m) for tf, m in TF_MINUTES.items() if data.get(tf)}
    m5 = data["m5"]
    store = SignalStore(path=None, ttl=10 ** 9, log_path=None)   # in memory, no expiry: the replay closes trades itself
    trades, blocked, evaluated = [], Counter(), 0
    open_trade = None                                             # {'close_ts', 'state'}
    for i in range(start, len(m5) - 1):
        now = int(m5[i]["time"].timestamp()) + 300 + 20
        if open_trade and now >= open_trade["close_ts"]:
            store.close(market, open_trade["state"], open_trade["close_ts"])
            open_trade = None
        if open_trade:
            continue
        if progress and i % 5000 == 0:
            progress(i, len(m5))
        view = {tf: s.at(now) for tf, s in series.items()}
        evaluated += 1
        res = sl.process(market, view, now, store, cfg, spread, commission, "HISTORICAL")
        if res["action"] == "WAIT":
            blocked[_reason_key(res)] += 1
            continue
        setup = res["setup"]
        out = simulate(setup, m5[i + 1:], setup["cost"])
        if out is None:                                           # the data ends before it resolves
            store.close(market, "manual", now)
            break
        result, r, used = out
        close_ts = int(m5[i + used]["time"].timestamp()) + 300
        open_trade = {"close_ts": close_ts, "state": "TP_HIT" if result == "win" else "SL_HIT"}
        trades.append({"time": int(m5[i]["time"].timestamp()), "market": market, "side": setup["side"],
                       "mode": setup.get("mode"), "result": result, "R": r,
                       "score100": setup.get("score100"), "bars": used})
    return {"trades": trades, "blocked": blocked, "evaluated": evaluated, "signals": len(trades)}


def _reason_key(res):
    """A short, number-free label for why the engine said WAIT (the first reason)."""
    why = res["reasons"][0] if res["reasons"] else "no reason recorded"
    why = re.sub(r"[-+]?\d+(\.\d+)?", "#", why)
    return why[:90]


# ===== Metrics =====

def metrics(trades):
    n = len(trades)
    if not n:
        return {"n": 0, "note": "no trades: nothing can be concluded"}
    wins = [t["R"] for t in trades if t["R"] > 0]
    losses = [-t["R"] for t in trades if t["R"] <= 0]
    equity = peak = dd = 0.0
    for t in trades:
        equity += t["R"]
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return {"n": n, "wins": len(wins), "losses": len(losses), "win_rate": len(wins) / n,
            "profit_factor": (sum(wins) / sum(losses)) if losses else None,
            "expectancy_R": equity / n, "avg_R": equity / n, "total_R": equity,
            "max_drawdown_R": dd, "small_sample": n < MIN_TRADES}


def breakdown(trades, key):
    keys = sorted({str(t.get(key)) for t in trades})
    return {k: metrics([t for t in trades if str(t.get(key)) == k]) for k in keys}


def score_bucket(t):
    s = t.get("score100") or 0
    return "90+" if s >= 90 else "86-89" if s >= 86 else "below 86"


def split_report(trades, split=0.7):
    """Time-ordered hold-out: the first `split` of the trades vs the rest. Nothing is tuned on the first
    part, so this only shows whether the result holds on later data."""
    cut = int(len(trades) * split)
    return {"in_sample": metrics(trades[:cut]), "out_of_sample": metrics(trades[cut:])}


def windows_report(trades, parts=4):
    """Consecutive time windows of the same length. No parameter is fitted, so this measures STABILITY
    over time (a result carried by one lucky stretch shows up as one good window and the rest bad)."""
    if not trades:
        return {"windows": [], "profitable_windows": 0}
    t0, t1 = trades[0]["time"], trades[-1]["time"] + 1
    step = (t1 - t0) / parts
    out = []
    for k in range(parts):
        a, b = t0 + step * k, t0 + step * (k + 1)
        part = [t for t in trades if a <= t["time"] < b]
        m = metrics(part)
        m["from"] = datetime.fromtimestamp(a, timezone.utc).strftime("%Y-%m-%d")
        m["to"] = datetime.fromtimestamp(b, timezone.utc).strftime("%Y-%m-%d")
        out.append(m)
    return {"windows": out, "profitable_windows": sum(1 for m in out if m.get("total_R", 0) > 0)}


def full_report(market, result, bars_info):
    trades = result["trades"]
    return {
        "market": market, "data": bars_info, "engine_profile": bars_info.get("profile"),
        "evaluated_candles": result["evaluated"], "signals": result["signals"],
        "overall": metrics(trades),
        "by_mode": breakdown(trades, "mode"), "by_side": breakdown(trades, "side"),
        "by_score": {k: metrics([t for t in trades if score_bucket(t) == k])
                     for k in sorted({score_bucket(t) for t in trades})},
        "out_of_sample": split_report(trades), "walk_forward_windows": windows_report(trades),
        "why_blocked_top10": result["blocked"].most_common(10),
        "sample_ok": len(trades) >= MIN_TRADES,
        "warning": (f"{len(trades)} trades: below the {MIN_TRADES} minimum, so these numbers are NOT "
                    "evidence of anything" if len(trades) < MIN_TRADES else
                    "enough trades to read, but a past result does not promise a future one"),
    }


def format_report(rep):
    o = rep["overall"]
    lines = [f"BACKTEST {rep['market']}  ({rep['data'].get('from')} -> {rep['data'].get('to')}, "
             f"{rep['data'].get('m5_bars')} M5 bars, profile {rep['engine_profile']})",
             f"Evaluated {rep['evaluated_candles']} candles, {rep['signals']} signals.",
             "WARNING: " + rep["warning"]]
    if o["n"]:
        pf = "n/a" if o["profit_factor"] is None else f"{o['profit_factor']:.2f}"
        lines += [f"Overall: win rate {o['win_rate']:.0%}, profit factor {pf}, expectancy {o['expectancy_R']:+.2f}R, "
                  f"total {o['total_R']:+.1f}R, max drawdown {o['max_drawdown_R']:.1f}R"]
        for name in ("by_mode", "by_side", "by_score"):
            for k, m in rep[name].items():
                lines.append(f"  {name[3:]} {k}: n={m['n']}, win {m.get('win_rate', 0):.0%}, "
                             f"{m.get('total_R', 0):+.1f}R")
        oos = rep["out_of_sample"]
        lines.append(f"In-sample n={oos['in_sample']['n']} {oos['in_sample'].get('total_R', 0):+.1f}R | "
                     f"Out-of-sample n={oos['out_of_sample']['n']} {oos['out_of_sample'].get('total_R', 0):+.1f}R")
        wf = rep["walk_forward_windows"]
        lines.append(f"Time windows profitable: {wf['profitable_windows']}/{len(wf['windows'])}")
    lines.append("Why signals were blocked (top):")
    lines += [f"  {n:>6}  {why}" for why, n in rep["why_blocked_top10"]]
    return "\n".join(lines)


# ===== Command line =====

def main(argv=None):
    ap = argparse.ArgumentParser(description="Backtest the selective engine on real CSV candles.")
    ap.add_argument("--market", required=True, help="XAUUSD, NAS100, US30 or BTCUSD (a label for the report)")
    ap.add_argument("--m5", required=True, help="CSV of M5 candles")
    ap.add_argument("--d1", help="optional CSV of D1 candles (else built from M5)")
    ap.add_argument("--tz-offset", type=float, default=0.0, help="hours the CSV clock is ahead of UTC")
    ap.add_argument("--spread", type=float, default=None, help="spread in PRICE units (None = unknown -> WAIT)")
    ap.add_argument("--commission", type=float, default=0.0, help="commission per round trip, price units")
    ap.add_argument("--profile", default="strict", choices=list(sl.PROFILES))
    ap.add_argument("--out", help="write the full report as JSON")
    a = ap.parse_args(argv)
    m5, problems = load_csv(a.m5, a.tz_offset)
    d1 = load_csv(a.d1, a.tz_offset)[0] if a.d1 else None
    if len(m5) < 500:
        sys.exit(f"only {len(m5)} usable M5 candles: too few to test anything")
    if problems:
        print(f"{len(problems)} bad rows skipped, first: {problems[0]}", file=sys.stderr)
    cfg = sl.make_config(a.profile)
    result = replay(a.market, build_data(m5, d1), cfg, a.spread, a.commission,
                    progress=lambda i, n: print(f"  {i}/{n}", file=sys.stderr))
    info = {"from": m5[0]["time"].strftime("%Y-%m-%d"), "to": m5[-1]["time"].strftime("%Y-%m-%d"),
            "m5_bars": len(m5), "bad_rows": len(problems), "tz_offset_hours": a.tz_offset,
            "spread": a.spread, "commission": a.commission, "profile": a.profile}
    rep = full_report(a.market, result, info)
    print(format_report(rep))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(rep, f, indent=1, default=str)


if __name__ == "__main__":
    main()
