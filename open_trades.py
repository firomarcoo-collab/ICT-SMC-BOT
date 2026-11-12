"""Shows the signals the bot is still following (not yet at TP or SL) and today's results.
Run: python open_trades.py
These are the BOT's signals, not your real MT5 positions (the bot never sees your account)."""
import json
import os
import sys
import time

import signal_store


def describe(path=None):
    path = path or os.environ.get("STATE_FILE", signal_store.DEFAULT_FILE)
    if not os.path.exists(path):
        return "No state file yet: the bot has not issued any signal."
    with open(path) as f:
        data = json.load(f)
    active, results = data.get("active", {}), data.get("results", [])
    lines = []
    if not active:
        lines.append("No active signals.")
    for _id, s in active.items():
        market = s.get("market", _id)
        age = int((time.time() - s["created"]) / 60)
        lines.append(f"{market}: {s['side'].upper()} entry {s['entry']:.8g}  SL {s['sl']:.8g}  "
                     f"TP {s['tp']:.8g}  (issued {age} min ago, still being followed)")
    day = time.strftime("%Y-%m-%d", time.gmtime())
    today = [r for r in results if time.strftime("%Y-%m-%d", time.gmtime(r["t"])) == day]
    if today:
        total = sum(r["R"] for r in today)
        lines.append(f"Today (UTC): {len(today)} finished, {sum(r['outcome'] == 'win' for r in today)} wins, "
                     f"{sum(r['outcome'] == 'loss' for r in today)} losses, {total:+.1f}R")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe(sys.argv[1] if len(sys.argv) > 1 else None))
