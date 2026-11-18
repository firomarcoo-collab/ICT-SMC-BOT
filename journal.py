"""Trade journal next to results.csv: one row per finished trade with MFE / MAE (in R) and the per-market report.
results.csv is untouched (older files keep their 7 columns). Run:  python journal.py"""
import csv
import os
import sys


DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.csv")

COLUMNS = ["time_utc", "symbol", "timeframe", "kind", "side", "result", "R", "mfe_r", "mae_r", "entry", "sl", "tp",
           "entry_utc", "session", "profile", "mode"]
FALSE_SIGNAL_MFE = 0.5          # a loss that never got 0.5R in favour counts as a false signal


def path_for(results_path):
    return os.path.join(os.path.dirname(os.path.abspath(results_path)), "journal.csv")


def _upgrade(path):
    """An older journal.csv has fewer columns: rewrite it once with the current header (new columns empty)."""
    try:
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            rows = list(reader)
    except OSError:
        return
    if header and header != COLUMNS:
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(COLUMNS)
            for r in rows:
                w.writerow([r.get(c, "") for c in COLUMNS])


def session_of(ts):
    """The New York based session label of a UTC timestamp (daylight saving handled), or 'unknown'."""
    try:
        import ny_session
        return ny_session.phase(int(float(ts)))
    except Exception:
        return "unknown"


def append(path, row):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        _upgrade(path)
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(COLUMNS)
        w.writerow([row.get(c, "") for c in COLUMNS])


def load(path):
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def metrics(rows):
    """Pure numbers for a list of journal rows (no guesses: empty input gives zeros)."""
    n = len(rows)
    rs = [float(r["R"]) for r in rows]
    gain, loss = sum(x for x in rs if x > 0), -sum(x for x in rs if x < 0)
    peak = equity = dd = 0.0
    for x in rs:
        equity += x
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    losses = [r for r in rows if float(r["R"]) <= 0]
    false = [r for r in losses if float(r.get("mfe_r") or 0) < FALSE_SIGNAL_MFE]
    avg = lambda k: sum(float(r.get(k) or 0) for r in rows) / n if n else 0.0      # noqa: E731
    return {"trades": n, "wins": n - len(losses), "win_rate": 100 * (n - len(losses)) / n if n else 0.0,
            "total_r": sum(rs), "avg_r": sum(rs) / n if n else 0.0,
            "profit_factor": gain / loss if loss else (float("inf") if gain else 0.0),
            "max_drawdown_r": dd, "avg_mfe": avg("mfe_r"), "avg_mae": avg("mae_r"),
            "false_signal_rate": 100 * len(false) / n if n else 0.0}


def line(label, rows):
    m = metrics(rows)
    pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
    return (f"{label}: {m['trades']} trades | win {m['win_rate']:.0f}% | {m['total_r']:+.1f}R | avg {m['avg_r']:+.2f}R | PF {pf} | "
            f"max DD {m['max_drawdown_r']:.1f}R | MFE {m['avg_mfe']:.2f}R | MAE {m['avg_mae']:.2f}R | false signals {m['false_signal_rate']:.0f}%")


NY_PHASES = ("New York open", "New York")


def report(rows, by=("symbol",)):
    """by: any of symbol, session, profile, mode, side. 'session' also adds one combined New York line."""
    if not rows:
        return "No finished trades in the journal yet."
    out = [line("ALL", rows)]
    for key in by:
        out.append(f"-- by {key}")
        for val in sorted({(r.get(key) or "unknown") for r in rows}):
            out.append("  " + line(val, [r for r in rows if (r.get(key) or "unknown") == val]))
        if key == "session":
            ny = [r for r in rows if r.get("session") in NY_PHASES]
            if ny:
                out.append("  " + line("New York (all)", ny))
    if len(rows) < 30:
        out.append(f"Only {len(rows)} trades: too few to judge the strategy (about 30+ per market are needed).")
    return "\n".join(out)


if __name__ == "__main__":
    keys = [a for a in sys.argv[1:] if a in ("symbol", "session", "profile", "mode", "side")]
    print(report(load(path_for(DEFAULT_CSV)), tuple(keys) or ("symbol", "session", "profile")))
