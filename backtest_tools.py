"""Backtest honesty tools: trading costs, an out-of-sample split and walk-forward folds. Pure functions over trade lists
([{'R', 'risk', 'time', ...}] oldest first). Costs are in PRICE units and are converted to R with each trade's own stop distance.
Nothing here predicts the future: a result is evidence about the past only, and fewer than ~30 trades says very little."""
from __future__ import annotations

MIN_TRADES = 30


def apply_costs(trades, spread=0.0, commission=0.0, slippage=0.0):
    """Returns new trades with R reduced by (spread + commission + slippage) / risk. A trade with no known risk is dropped from the result and
    counted in the second return value (never silently treated as free)."""
    out, skipped = [], 0
    for t in trades:
        risk = t.get("risk")
        if not risk or risk <= 0:
            skipped += 1
            continue
        cost_r = (spread + commission + slippage) / risk
        out.append(dict(t, R=t["R"] - cost_r, cost_r=cost_r))
    return out, skipped


def stats(trades):
    n = len(trades)
    rs = [t["R"] for t in trades]
    gain, loss = sum(x for x in rs if x > 0), -sum(x for x in rs if x < 0)
    peak = eq = dd = 0.0
    for x in rs:
        eq += x
        peak, dd = max(peak, eq), max(dd, peak - eq)
    return {"trades": n, "total_r": sum(rs), "avg_r": sum(rs) / n if n else 0.0, "win_rate": 100 * sum(1 for x in rs if x > 0) / n if n else 0.0,
            "profit_factor": gain / loss if loss else (float("inf") if gain else 0.0), "max_drawdown_r": dd}


def split_oos(trades, in_sample=0.7):
    """Chronological split: the first part is where rules may be tuned, the last part is never looked at while tuning."""
    k = int(len(trades) * in_sample)
    return trades[:k], trades[k:]


def walk_forward(trades, folds=4):
    """Consecutive non-overlapping windows in time order. Returns a list of stats dicts; a stable strategy keeps a similar sign in every window."""
    if folds < 2 or len(trades) < folds:
        return []
    size = len(trades) // folds
    return [dict(stats(trades[i * size:(i + 1) * size if i < folds - 1 else len(trades)]), fold=i + 1) for i in range(folds)]


def fmt(label, s):
    pf = "inf" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
    return (f"{label}: {s['trades']} trades | win {s['win_rate']:.0f}% | {s['total_r']:+.1f}R | avg {s['avg_r']:+.2f}R | PF {pf} | "
            f"max DD {s['max_drawdown_r']:.1f}R" + (" | too few trades to judge" if s["trades"] < MIN_TRADES else ""))


def honest_report(trades, spread=0.0, commission=0.0, slippage=0.0, folds=4, in_sample=0.7):
    """Text block: before costs, after costs, in-sample vs out-of-sample, walk-forward windows."""
    if not trades:
        return "No trades to report."
    costed, skipped = apply_costs(trades, spread, commission, slippage)
    lines = [fmt("Before costs", stats(trades)), fmt("After costs", stats(costed))]
    if skipped:
        lines.append(f"({skipped} trades had no stop distance and are left out of the cost run)")
    a, b = split_oos(costed, in_sample)
    lines += [fmt("In-sample (first %d%%)" % round(in_sample * 100), stats(a)), fmt("Out-of-sample (last part)", stats(b))]
    for w in walk_forward(costed, folds):
        lines.append(fmt(f"Walk-forward window {w['fold']}", w))
    lines.append("Past results do not predict future results. No profit or win rate is promised.")
    return "\n".join(lines)
