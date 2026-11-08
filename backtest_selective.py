"""Replays the selective engine over HISTORICAL candles, bar by bar (no look-ahead: at each M5
close only candles that had already closed are visible). Costs are charged on every trade.

Trade rules: the signal is a market entry at the confirmation candle's close (the cost of
spread / commission / slippage is charged separately). The following candles are checked stop
first, then target (a candle that touches both counts as a loss). One trade at a time per run
(the market lock).
R net = (reward - cost) / risk for a win, -(risk + cost) / risk for a loss.

The data is split in time: the first `split` of the signals is "in-sample", the rest
"out-of-sample". No parameter is tuned here, so the out-of-sample part is a plain hold-out
check, not walk-forward optimisation. A small sample proves nothing: read `n` first.

Usage (needs candles from files or your own loader):
    from backtest_selective import replay, summarize
    trades = replay(data, selective.SelectiveConfig(), spread=0.3)
    print(summarize(trades))
"""
import selective as sl



def _simulate(setup, future, cost):
    """future: M5 bars after the signal (oldest first). Returns (result, R_net, bars_used) or None."""
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
    return None  # still open when the data ends


def replay(data, cfg=None, spread=None, commission_price=None, calendar="utc", start=60):
    """data = {'h4','h1','m15','m5'} oldest first. Returns a list of trade dicts."""
    cfg = cfg or sl.SelectiveConfig()
    cfg = sl.SelectiveConfig(**{**cfg.__dict__, "allow_historical": True})
    m5 = data["m5"]
    trades, busy_until = [], -1
    for i in range(start, len(m5)):
        if i <= busy_until:
            continue
        now = int(m5[i]["time"].timestamp()) + 300 + 20
        res = sl.evaluate("BT", dict(data, m5=m5[:i + 1]), now, cfg, spread, commission_price,
                          "HISTORICAL", calendar)
        if res["action"] == "WAIT":
            continue
        out = _simulate(res["setup"], m5[i + 1:], res["setup"]["cost"])
        if out is None:
            break
        result, r, used = out
        busy_until = i + used
        trades.append({"time": int(m5[i]["time"].timestamp()), "side": res["setup"]["side"],
                       "result": result, "R": r, "score": res["score"]})
    return trades


def summarize(trades):
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
    return {"n": n, "win_rate": len(wins) / n, "profit_factor": (sum(wins) / sum(losses)) if losses else None,
            "expectancy_R": sum(t["R"] for t in trades) / n, "max_drawdown_R": dd,
            "total_R": equity, "small_sample": n < 100}


def split_report(trades, split=0.7):
    cut = int(len(trades) * split)
    return {"in_sample": summarize(trades[:cut]), "out_of_sample": summarize(trades[cut:])}
