"""Summary of results.csv: all trades, wins, losses, win rate and total R.

Usage:  python stats.py           (everything)
        python stats.py kind      (also split by strategy)   other options: symbol, timeframe
"""
import csv
import sys

from tracker import DEFAULT_CSV


def load(path=DEFAULT_CSV):
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def line(label, rows):
    wins = sum(1 for r in rows if r["result"] == "win")
    losses = len(rows) - wins
    total = sum(float(r["R"]) for r in rows)
    rate = 100 * wins / len(rows) if rows else 0
    return (f"{label}: {len(rows)} trades | {wins} wins | {losses} losses | "
            f"win rate {rate:.0f}% | total {total:+.1f}R")


def report(rows, by=None):
    if not rows:
        return "No finished trades yet."
    out = [line("ALL", rows)]
    if by:
        for key in sorted({r[by] for r in rows}):
            out.append("  " + line(key, [r for r in rows if r[by] == key]))
    return "\n".join(out)


if __name__ == "__main__":
    by = sys.argv[1] if len(sys.argv) > 1 else None
    if by not in (None, "kind", "symbol", "timeframe"):
        raise SystemExit("Use: python stats.py [kind|symbol|timeframe]")
    print(report(load(), by))
