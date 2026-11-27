"""One signal lifecycle for the unified engine. States: WAIT, CONFIRMED, WARNING, INVALIDATED, EXPIRED, LOSS, TP_HIT.
A terminal state (INVALIDATED, EXPIRED, LOSS, TP_HIT) is final: a second result for the same signal is refused, so a result or a loss is never
reported or journaled twice. Pure functions over a plain dict (stored inside the unified state file)."""
from __future__ import annotations

TERMINAL = ("INVALIDATED", "EXPIRED", "LOSS", "TP_HIT")
ALLOWED = {
    "WAIT": ("CONFIRMED",),
    "CONFIRMED": ("WARNING", "INVALIDATED", "EXPIRED", "LOSS", "TP_HIT"),
    "WARNING": ("CONFIRMED", "INVALIDATED", "EXPIRED", "LOSS", "TP_HIT"),
}
OUTCOME = {"win": "TP_HIT", "tp_hit": "TP_HIT", "loss": "LOSS", "sl_hit": "LOSS", "cancelled": "INVALIDATED", "expired": "EXPIRED"}


def state(life, sid):
    return (life.get(sid) or {}).get("state", "WAIT")


def move(life, sid, new, now_ts, note=""):
    """True when the transition was applied; False when it is not allowed (terminal or impossible), which makes the caller stay silent."""
    cur = state(life, sid)
    if new == cur and new not in TERMINAL:
        return False
    if new not in ALLOWED.get(cur, ()):
        return False
    rec = life.setdefault(sid, {"state": "WAIT", "history": []})
    rec["state"] = new
    rec["history"].append([new, int(now_ts), note])
    return True


def from_outcome(outcome):
    return OUTCOME.get(str(outcome).lower())
