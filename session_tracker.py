"""Measures the alert-only detectors (ICT Session Scalper, SMC Prime, Order Flow Pulse) on closed M5 bars: information only.
A signal is OPEN until its entry is touched (limit entries) and then resolved: stop hit = -1R, TP1 hit = +R:R to TP1; when both
are inside one bar the stop is assumed first (the pessimistic reading). Unfilled signals expire without counting.
Results are appended to session_results.csv (read by review.py). Files: session_open.json, session_results.csv (SESSION_DIR)."""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime

MAX_BARS_OPEN = 288           # 24 h of M5 bars
MAX_BARS_UNFILLED = 48        # 4 h waiting for a limit entry
FIELDS = ["time", "market", "name", "side", "entry", "sl", "tp1", "R", "result", "bars", "profile", "symbol", "session", "mode"]


def _dir():
    return os.environ.get("SESSION_DIR", ".")


def open_file():
    return os.path.join(_dir(), "session_open.json")


def results_file():
    return os.path.join(_dir(), "session_results.csv")


def _load():
    try:
        with open(open_file()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(rows):
    try:
        with open(open_file(), "w") as f:
            json.dump(rows, f)
    except OSError:
        pass


def open_signal(market, ev):
    """Register a SIGNAL event. Pulse entries are market orders (filled at the signal), the others are limit entries."""
    rows = _load()
    sid = f"{market}-{ev['name']}-{ev['direction']}-{ev['bar_time']}"
    if any(r["id"] == sid for r in rows):
        return
    rows.append({"id": sid, "market": market, "name": ev["name"], "side": ev["direction"], "entry": ev["entry"], "sl": ev["sl"],
                 "tp1": ev["tp1"], "time": ev["bar_time"], "filled": "Pulse" in ev["name"], "bars": 0})
    _save(rows)


def update(market, bars):
    """Walk the closed bars newer than each open signal of this market. Returns the list of closed result rows."""
    rows, keep, done = _load(), [], []
    for r in rows:
        if r["market"] != market:
            keep.append(r)
            continue
        t0 = datetime.fromisoformat(r["time"])
        long = r["side"] == "long"
        res = None
        for b in bars:
            if b["time"] <= t0 or b["time"].isoformat() <= r.get("seen", ""):
                continue
            r["seen"] = b["time"].isoformat()
            r["bars"] += 1
            if not r["filled"]:
                if (b["low"] <= r["entry"]) if long else (b["high"] >= r["entry"]):
                    r["filled"] = True
                elif r["bars"] >= MAX_BARS_UNFILLED:
                    res = ("NOFILL", 0.0)
                    break
                else:
                    continue
            hit_sl = b["low"] <= r["sl"] if long else b["high"] >= r["sl"]
            hit_tp = b["high"] >= r["tp1"] if long else b["low"] <= r["tp1"]
            risk = abs(r["entry"] - r["sl"])
            if hit_sl:
                res = ("LOSS", -1.0)                                           # both in one bar: the stop is assumed first
                break
            if hit_tp:
                res = ("WIN", abs(r["tp1"] - r["entry"]) / risk if risk else 0.0)
                break
            if r["bars"] >= MAX_BARS_OPEN:
                mtm = ((b["close"] - r["entry"]) if long else (r["entry"] - b["close"])) / risk if risk else 0.0
                res = ("EXPIRED", mtm)
                break
        if res is None:
            keep.append(r)
            continue
        done.append({"time": r["time"], "market": r["market"], "name": r["name"], "side": r["side"], "entry": r["entry"], "sl": r["sl"],
                     "tp1": r["tp1"], "R": round(res[1], 3), "result": res[0], "bars": r["bars"], "profile": r["name"],
                     "symbol": r["market"], "session": "", "mode": ""})
    if done or len(keep) != len(rows) or rows:
        _save(keep)
    counted = [d for d in done if d["result"] != "NOFILL"]
    if counted:
        new = not os.path.exists(results_file())
        try:
            with open(results_file(), "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                if new:
                    w.writeheader()
                w.writerows(counted)
        except OSError:
            pass
    return done


def load_results():
    try:
        with open(results_file(), newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def line(r):
    return f"RESULT: {r['result']} {float(r['R']):+.1f}R | {r['market']} {r['name']} {r['side'].upper()} (entry {r['entry']:g}, SL {r['sl']:g}, TP1 {r['tp1']:g})"
