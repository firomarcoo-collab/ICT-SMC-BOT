"""Follows each entry signal after it is sent and reports WIN / LOSS / not filled.

Same rules as backtest.py: the order is a limit at `entry`; it fills when price touches it
(cancelled after MAX_WAIT_BARS bars); on the fill bar only the stop is checked; later bars
check the stop first, then the target. Results are appended to results.csv.
The state lives in memory: signals still open when the bot is stopped are forgotten.
"""
import csv
import os
from datetime import datetime, timezone

MAX_WAIT_BARS = 24
DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.csv")


class Tracker:
    def __init__(self, path=DEFAULT_CSV):
        self.path = path
        self.items = []
        self.on_done = None  # optional callback(item, outcome) with outcome win/loss/not_filled
        self.wins = self.losses = 0
        self.total_r = 0.0
        self._load()

    def _load(self):
        try:
            with open(self.path, newline="") as f:
                for row in csv.DictReader(f):
                    r = float(row["R"])
                    self._count(r)
        except (OSError, KeyError, ValueError):
            pass

    def _count(self, r):
        if r > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.total_r += r

    def tally(self):
        n = self.wins + self.losses
        rate = 100 * self.wins / n if n else 0
        return (f"Total: {n} trades, {self.wins} wins, {self.losses} losses "
                f"(win rate {rate:.0f}%, {self.total_r:+.1f}R)")

    def add(self, name, tf, kind, setup, bar_time, ref=None, market=False):
        """market=True: the trade is entered at once at `entry` (the signal candle's close), so
        it starts OPEN and the next candles are checked stop first. Otherwise it is a limit order."""
        self.items.append({"name": name, "tf": tf, "kind": kind, "setup": dict(setup),
                           "state": "open" if market else "pending", "waited": 0, "last": bar_time,
                           "ref": ref})

    def _finish(self, it, outcome):
        if self.on_done:
            try:
                self.on_done(it, outcome)
            except Exception:
                pass  # a callback problem must not stop the tracking

    def _record(self, it, result, r):
        self._count(r)
        try:
            new = not os.path.exists(self.path)
            with open(self.path, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["time_utc", "symbol", "timeframe", "kind", "side", "result", "R"])
                w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), it["name"],
                            it["tf"], it["kind"], it["setup"]["side"], result, f"{r:.2f}"])
        except OSError:
            pass  # a read-only folder must not stop the alerts

    def _label(self, it):
        s = it["setup"]
        return (f"{it['name']} {it['tf']} {it['kind'].upper()} {s['side'].upper()} "
                f"(entry {s['entry']}, SL {s['sl']}, TP {s['tp']})")

    def update(self, name, tf, r):
        """r: candles newest first (r[0] forming). Returns a list of messages to send."""
        notes = []
        closed = sorted(r[1:], key=lambda c: c["time"])
        for it in [i for i in self.items if i["name"] == name and i["tf"] == tf]:
            s = it["setup"]
            buy = s["side"] == "buy"
            risk = abs(s["entry"] - s["sl"])
            reward = abs(s["tp"] - s["entry"]) / risk if risk else 0.0
            for c in closed:
                t = int(c["time"].timestamp())
                if t <= it["last"]:
                    continue
                it["last"] = t
                stop_hit = c["low"] <= s["sl"] if buy else c["high"] >= s["sl"]
                target_hit = c["high"] >= s["tp"] if buy else c["low"] <= s["tp"]
                if it["state"] == "pending":
                    touched = c["low"] <= s["entry"] if buy else c["high"] >= s["entry"]
                    if not touched:
                        it["waited"] += 1
                        if it["waited"] >= MAX_WAIT_BARS:
                            it["state"] = "done"
                            self._finish(it, "not_filled")
                            notes.append(f"NOT FILLED: {self._label(it)}. Cancel the pending order.")
                            break
                        continue
                    it["state"] = "open"
                    target_hit = False  # on the fill bar only the stop counts
                if it["state"] == "open":
                    if stop_hit:
                        it["state"] = "done"
                        self._record(it, "loss", -1.0)
                        self._finish(it, "loss")
                        notes.append(f"RESULT: LOSS -1R | {self._label(it)}\n{self.tally()}")
                        break
                    if target_hit:
                        it["state"] = "done"
                        self._record(it, "win", reward)
                        self._finish(it, "win")
                        notes.append(f"RESULT: WIN +{reward:.1f}R | {self._label(it)}\n{self.tally()}")
                        break
        self.items = [i for i in self.items if i["state"] != "done"]
        return notes
