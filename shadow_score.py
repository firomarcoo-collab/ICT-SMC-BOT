"""Shadow score: the MASTER DIRECTIVE weights computed NEXT TO the live unified score. Information only: it never changes a live decision,
an alert or a tone. Both numbers are logged to shadow_scores.jsonl so they can be compared before any switch is decided.

Weights (100): H4/H1 trend 20, liquidity 20, BOS/CHoCH 15, FVG/OB 15, M15 confirmation 15, order flow 10, session + spread 5.
Rules: no double counting (each fact feeds one part); data that is not available is left OUT of the denominator (never a negative);
an estimated order flow (candle based, not real) can earn at most half of its part; a critical invalidation overrides the score."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

CAP = {"trend": 20, "liquidity": 20, "structure": 15, "zone": 15, "confirm": 15, "flow": 10, "session": 5}
MIN_SCORE = 80


def log_file():
    return os.environ.get("SHADOW_FILE", "shadow_scores.jsonl")


def enabled():
    return os.environ.get("SHADOW_SCORE", "1") != "0"


def compute(main, votes, now_ts, session_ok=None):
    """main: the stored main-chain signal (side, checks, parts, trends). votes: live module votes {'smc'|'scalper'|'pulse': vote}.
    session_ok: True / False / None (None = unknown, left out). Returns {computed, score, parts, left_out, flow, vetoes, note}."""
    out = {"computed": False, "score": None, "parts": {}, "left_out": [], "flow": "none", "vetoes": [], "note": ""}
    if not main:
        out["note"] = "no live main-chain signal, so there is nothing to score"
        return out
    side = "buy" if main["side"] in ("buy", "long") else "sell"
    want = "up" if side == "buy" else "down"
    c, p, tr = main.get("checks") or {}, main.get("parts") or {}, main.get("trends") or {}
    got, avail = {}, {}

    def put(part, pts, cap, known=True):
        if not known:
            out["left_out"].append(part)
            return
        got[part] = got.get(part, 0) + pts
        avail[part] = avail.get(part, 0) + cap

    for tf in ("h4", "h1"):                                           # trend 20: 10 per timeframe
        t = tr.get(tf)
        put("trend", 10 if t == want else 0, 10, t in ("up", "down", "range"))
    put("liquidity", 14 if c.get("liquidity") else 0, 14, "liquidity" in c)
    put("liquidity", 6 if (p.get("displacement") or 0) > 0 else 0, 6, "displacement" in p)
    put("structure", 15 if c.get("bos_mss") else 0, 15, "bos_mss" in c)
    put("zone", 10 if c.get("fvg_ob") else 0, 10, "fvg_ob" in c)
    put("zone", 5 if c.get("retest") else 0, 5, "retest" in c)
    put("confirm", 9 if c.get("confirm") else 0, 9, "confirm" in c)
    t15 = tr.get("m15")
    put("confirm", 6 if t15 == want else 0, 6, t15 in ("up", "down", "range"))
    pulse = (votes or {}).get("pulse")                                # order flow: only a candle estimate exists here
    if pulse and pulse.get("side"):
        real = False                                                  # no real order flow feed exists in this project: always an estimate
        out["flow"] = "real" if real else "estimated"
        cap = 10 if real else 5
        put("flow", cap if pulse["side"] == side else 0, cap)
    else:
        out["left_out"].append("flow")
    if session_ok is None and "cost" not in c:
        out["left_out"].append("session")
    else:
        put("session", 3 if session_ok else 0, 3, session_ok is not None)
        put("session", 2 if c.get("cost") else 0, 2, "cost" in c)
    top = sum(avail.values())
    if top <= 0:
        out["note"] = "no scoring data available"
        return out
    out["parts"] = {k: (round(got.get(k, 0), 1), avail[k]) for k in avail}
    out["score"] = round(100.0 * sum(got.values()) / top, 1)
    out["computed"] = True
    out["data_share"] = round(100.0 * top / sum(CAP.values()))      # how much of the 100 points had data behind it
    opp = [k for k, v in (votes or {}).items() if v.get("side") and v["side"] != side]
    if opp:
        out["vetoes"].append("module(s) against the direction: " + ", ".join(opp))
    try:
        import unified_engine
        lost = unified_engine.mandatory(main)
        if lost:
            out["vetoes"].append("mandatory condition missing: " + ", ".join(lost))
    except Exception:                                                  # noqa: BLE001
        pass
    return out


def verdict(res):
    if not res["computed"]:
        return "NOT COMPUTED"
    if res["vetoes"]:
        return "VETO"
    return "PASS" if res["score"] >= MIN_SCORE else "BELOW MINIMUM"


def log(market, now_ts, shadow, live_decision, live_score):
    """One line per change of the pair (live decision, shadow verdict) for a market. Never raises."""
    try:
        row = {"ts": int(now_ts), "market": market, "live": live_decision, "live_score": live_score,
               "shadow": shadow["score"], "shadow_verdict": verdict(shadow), "flow": shadow["flow"],
               "vetoes": shadow["vetoes"], "left_out": shadow["left_out"]}
        key = (row["live"], row["shadow_verdict"], row["shadow"])
        last = None
        try:
            with open(log_file()) as f:
                for line in f:
                    r = json.loads(line)
                    if r.get("market") == market:
                        last = (r["live"], r["shadow_verdict"], r["shadow"])
        except (OSError, ValueError):
            pass
        if last != key:
            with open(log_file(), "a") as f:
                f.write(json.dumps(row) + "\n")
        return True
    except Exception:                                                  # noqa: BLE001
        return False


def summary(limit=2000):
    """Agreement between the live decision and the shadow verdict over the logged lines."""
    rows = []
    try:
        with open(log_file()) as f:
            rows = [json.loads(x) for x in f.readlines()[-limit:]]
    except (OSError, ValueError):
        pass
    scored = [r for r in rows if r["shadow_verdict"] != "NOT COMPUTED"]
    live_yes = [r for r in scored if str(r["live"]).startswith("CONFIRMED")]
    sh_yes = [r for r in scored if r["shadow_verdict"] == "PASS"]
    both = [r for r in live_yes if r["shadow_verdict"] == "PASS"]
    return {"rows": len(rows), "scored": len(scored), "live_confirmed": len(live_yes), "shadow_pass": len(sh_yes), "both": len(both)}
