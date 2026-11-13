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
# Trade management alerts (messages only, nothing is ever sent to the broker):
BE_ARM_R = 0.5       # once the trade has been this many R in profit ...
RETRACE_FRAC = 0.5   # ... and a closed candle gives back this share of that best profit: move SL to entry
MANAGE_PREFIXES = ("TRADE +", "REVERSAL WARNING", "CLOSE NOW")   # messages that are management alerts, not results
CLOSE_R = 0.1         # a trade that was in profit and is back at this many R or less: close it
ADD_R = 1.0          # a candle CLOSES this many R in profit: milestone (protect + optional add-on)
DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.csv")


class Tracker:
    def __init__(self, path=DEFAULT_CSV, be_arm_r=None, retrace_frac=None, add_r=None):
        self.path = path
        self.be_arm_r = BE_ARM_R if be_arm_r is None else be_arm_r
        self.retrace_frac = RETRACE_FRAC if retrace_frac is None else retrace_frac
        self.add_r = ADD_R if add_r is None else add_r
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
                           "ref": ref, "mfe": 0.0, "warned": False, "milestone": False,
                           "close_warned": False})

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
                f"(entry {s['entry']:.8g}, SL {s['sl']:.8g}, TP {s['tp']:.8g})")

    def _manage(self, it, c, buy, risk):
        """Alerts for an OPEN trade after a closed candle that hit neither stop nor target.
        1) +ADD_R closed in profit: protect (stop to entry) and mention a possible add-on.
        2) The trade was at least BE_ARM_R in profit, a closed candle gave back RETRACE_FRAC of the best
           profit and is still in profit: reversal warning, stop to entry."""
        if not risk:
            return []
        s = it["setup"]
        fav = ((c["high"] - s["entry"]) if buy else (s["entry"] - c["low"])) / risk
        now_r = ((c["close"] - s["entry"]) if buy else (s["entry"] - c["close"])) / risk
        it["mfe"] = max(it["mfe"], fav)
        out = []
        if now_r >= self.add_r and not it["milestone"]:
            it["milestone"] = True
            out.append(
                f"TRADE +{now_r:.1f}R: {self._label(it)}\n"
                f"-> Move the stop to the entry {s['entry']:.8g} (risk-free). A reinforcement (add-on) in the "
                f"same direction is only worth considering after a pullback and a NEW closed M5 confirmation, "
                f"with a smaller size and its own stop.\n"
                f"الصفقة +{now_r:.1f}R: حرّك الستوب إلى سعر الدخول. التعزيز بنفس الاتجاه فقط بعد تراجع وشمعة M5 تأكيد جديدة وبحجم أصغر.")
        elif (it["mfe"] >= self.be_arm_r and not it["warned"] and not it["milestone"]
              and 0 < now_r <= it["mfe"] * (1 - self.retrace_frac)):
            it["warned"] = True
            out.append(
                f"REVERSAL WARNING: {self._label(it)}\n"
                f"Best profit was +{it['mfe']:.1f}R, now +{now_r:.1f}R and turning back. "
                f"-> Consider moving the stop to the entry {s['entry']:.8g} so it cannot become a loss.\n"
                f"تحذير انعكاس: الربح الأعلى كان +{it['mfe']:.1f}R والآن +{now_r:.1f}R. فكّر بتحريك الستوب إلى سعر الدخول.")
        if (it["mfe"] >= self.be_arm_r and not it.get("close_warned") and now_r <= CLOSE_R):
            it["close_warned"] = True
            out.append(
                f"CLOSE NOW: {self._label(it)}\n"
                f"The profit (best +{it['mfe']:.1f}R) is gone, price is back at the entry ({now_r:+.1f}R). "
                f"-> Close the trade by hand if you did not move the stop (or keep the stop at the entry).\n"
                f"أقفل الصفقة: الربح (الأعلى +{it['mfe']:.1f}R) ضاع والسعر رجع للدخول ({now_r:+.1f}R). "
                f"أقفلها يدويًا إذا لم تحرّك الستوب.")
        return out

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
                fill_bar = False
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
                    fill_bar = True     # ... and it is too early to manage the trade
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
                    if not fill_bar:
                        notes.extend(self._manage(it, c, buy, risk))
        self.items = [i for i in self.items if i["state"] != "done"]
        return notes
