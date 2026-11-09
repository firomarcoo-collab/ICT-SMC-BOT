"""Persistent signal state: one ACTIVE signal per market, no duplicates, no BUY/SELL conflicts.

State lives in a JSON file written atomically, so a restart keeps every lock. Each market has
its own lock (XAUUSD does not block BTCUSD). A signal stays active until it is closed
(win / loss / not filled / expired / manual release). Every change is appended to a jsonl log.
"""
import json
import os
import time

DEFAULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals_state.json")
MAX_SEEN = 1000
DEFAULT_TTL = 6 * 3600  # an active signal older than this is expired (tracker normally closes it first)


class SignalStore:
    def __init__(self, path=DEFAULT_FILE, ttl=DEFAULT_TTL, log_path=None):
        self.path = path
        self.ttl = ttl
        self.log_path = log_path if log_path is not None else (path + "l" if path else None)
        self.active_signals = {}
        self.seen = []
        self.results = []   # finished trades [{'t', 'outcome', 'R'}]: feeds the daily risk guard
        self.used = []      # liquidity events and FVG/OB zones that already produced a signal
        self.load_error = None
        self._load()

    def _load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                data = json.load(f)
            self.active_signals = dict(data.get("active", {}))
            self.seen = list(data.get("seen", []))[-MAX_SEEN:]
            self.used = list(data.get("used", []))[-MAX_SEEN:]
            self.results = list(data.get("results", []))[-200:]
        except (OSError, ValueError, AttributeError) as e:
            self.load_error = str(e)
            try:
                os.replace(self.path, self.path + ".bad")  # keep the broken file, start clean
            except OSError:
                pass

    def _save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"active": self.active_signals, "seen": self.seen[-MAX_SEEN:],
                       "used": self.used[-MAX_SEEN:], "results": self.results[-200:]}, f)
        os.replace(tmp, self.path)

    def _log(self, event, sig, now_ts, extra=None):
        if not self.log_path:
            return
        row = {"time": now_ts, "event": event, "signal": sig}
        if extra:
            row.update(extra)
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            pass

    def active(self, market):
        return self.active_signals.get(market)

    def open(self, market, setup, now_ts=None):
        """Try to take the market's lock. Returns (ok, reason)."""
        now_ts = int(now_ts if now_ts is not None else time.time())
        self.expire(now_ts)
        sid = f"{market}-{setup['side']}-{setup['bar_time']}"
        cur = self.active_signals.get(market)
        if cur:
            kind = "duplicate" if cur["side"] == setup["side"] else "conflicting"
            return False, f"{kind} signal blocked: {market} already has an active {cur['side'].upper()} ({cur['id']})"
        if sid in self.seen:
            return False, f"duplicate signal blocked: {sid} was already issued"
        uses = [f"{market}|{u}" for u in setup.get("uses", [])]  # ids are per market
        reused = [u for u in uses if u in self.used]
        if reused:
            return False, f"already used (consumed liquidity event or zone): {reused[0].split('|', 1)[1]}"
        sig = {"id": sid, "market": market, "side": setup["side"], "entry": setup["entry"],
               "sl": setup["sl"], "tp": setup["tp"], "bar_time": setup["bar_time"],
               "created": now_ts, "state": "ACTIVE"}
        self.active_signals[market] = sig
        self.seen.append(sid)
        self.used.extend(uses)
        self._save()
        self._log("OPEN", sig, now_ts)
        return True, ""

    def close(self, market, outcome, now_ts=None):
        """Release the market's lock. outcome: win, loss, not_filled, expired, manual."""
        now_ts = int(now_ts if now_ts is not None else time.time())
        sig = self.active_signals.pop(market, None)
        if sig:
            if outcome in ("win", "loss"):
                risk = abs(sig["entry"] - sig["sl"])
                r = (abs(sig["tp"] - sig["entry"]) / risk if risk else 0.0) if outcome == "win" else -1.0
                self.results.append({"t": now_ts, "outcome": outcome, "R": r, "market": market})
            self._save()
            self._log("CLOSE", sig, now_ts, {"outcome": outcome})
        return sig

    def halted(self, now_ts, max_losses=2, max_loss_r=3.0):
        """Reason string when today's (UTC) results say stop, else ''. Counts all markets together:
        `max_losses` losses in a row, or a day total of -`max_loss_r` R or worse. Costs are not included."""
        day = time.strftime("%Y-%m-%d", time.gmtime(now_ts))
        today = [x for x in self.results if time.strftime("%Y-%m-%d", time.gmtime(x["t"])) == day]
        streak = 0
        for x in reversed(today):
            if x["outcome"] != "loss":
                break
            streak += 1
        if max_losses and streak >= max_losses:
            return f"daily risk guard: {streak} losses in a row today, no new signals until tomorrow (UTC)"
        total = sum(x["R"] for x in today)
        if max_loss_r and total <= -max_loss_r:
            return f"daily risk guard: today's result is {total:.1f}R, no new signals until tomorrow (UTC)"
        return ""

    def expire(self, now_ts):
        for market, sig in list(self.active_signals.items()):
            if now_ts - sig["created"] > self.ttl:
                self.close(market, "expired", now_ts)
