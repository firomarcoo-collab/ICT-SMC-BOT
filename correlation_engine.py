"""Correlation engine: correlated markets are one risk, not two. Signals only.
PAIRS holds ASSUMED relationships from general market structure (sign +1 = move together, -1 = move opposite); they are not measured by
this project. `measured()` computes a real Pearson correlation of returns from candles when you want to check an assumption.
effective_side(): a BUY on a market with sign -1 against the group's reference counts like a SELL there, so two signals carry the same risk when
(side_a * sign) is the same as side_b."""
from __future__ import annotations

import os

PAIRS = {
    ("US100", "US30"): 1, ("US100", "DAX40"): 1, ("US30", "DAX40"): 1, ("US100", "BTCUSD"): 1,
    ("EURUSD", "GBPUSD"): 1, ("EURUSD", "USDJPY"): -1, ("GBPUSD", "USDJPY"): -1,
    ("XAUUSD", "EURUSD"): 1, ("XAUUSD", "GBPUSD"): 1, ("XAUUSD", "USDJPY"): -1,
    ("GBPJPY", "GBPUSD"): 1, ("GBPJPY", "USDJPY"): 1,
}
ASSUMED = "assumed relationship, not measured by this project"


def enabled():
    return os.environ.get("CORR_GATE", "1") != "0"


def sign(a, b):
    """+1 / -1 for a known pair, 0 when the two markets are treated as independent."""
    if a == b:
        return 1
    return PAIRS.get((a, b)) or PAIRS.get((b, a)) or 0


def _dir(side):
    return 1 if str(side).lower() in ("buy", "long") else -1


def overlaps(market, side, others):
    """others: iterable of (market, side) already active. Returns [{'market', 'side', 'kind': 'same risk' | 'opposite'}] for correlated ones."""
    out = []
    for m, s in others:
        if m == market:
            continue
        sg = sign(market, m)
        if not sg:
            continue
        same = _dir(side) * sg == _dir(s)
        out.append({"market": m, "side": str(s).upper(), "kind": "same risk" if same else "opposite"})
    return out


def block_reason(market, side, active):
    """A text when a correlated active signal already carries the same risk, else ''."""
    if not enabled():
        return ""
    same = [o for o in overlaps(market, side, active) if o["kind"] == "same risk"]
    if not same:
        return ""
    return "correlated with an active " + ", ".join(f"{o['market']} {o['side']}" for o in same) + " (same risk, counted once; " + ASSUMED + ")"


def note(market, side, active):
    opp = [o for o in overlaps(market, side, active) if o["kind"] == "opposite"]
    return ("correlated markets pull the other way: " + ", ".join(f"{o['market']} {o['side']}" for o in opp)) if opp else ""


def measured(closes_a, closes_b):
    """Pearson correlation of the simple returns of two equally long close series (oldest first); None when it cannot be computed."""
    n = min(len(closes_a), len(closes_b))
    if n < 10:
        return None
    a, b = closes_a[-n:], closes_b[-n:]
    ra = [(a[i] - a[i - 1]) / a[i - 1] for i in range(1, n) if a[i - 1]]
    rb = [(b[i] - b[i - 1]) / b[i - 1] for i in range(1, n) if b[i - 1]]
    k = min(len(ra), len(rb))
    if k < 9:
        return None
    ra, rb = ra[-k:], rb[-k:]
    ma, mb = sum(ra) / k, sum(rb) / k
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return None if va <= 0 or vb <= 0 else cov / (va * vb) ** 0.5
