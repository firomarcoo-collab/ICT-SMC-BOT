"""PROFESSIONAL TRADE ANALYSIS ENGINE: the fourth brain. Signals only; it never places an order and it never changes the three strategies.
It re-reads the price data on its own (H4 / H1 for direction and liquidity, M15 for confirming the entry) with SMA/EMA 50 and 200, RSI,
Bollinger Bands, MACD, support and resistance, Fibonacci and market structure, and judges a candidate that the unified engine already holds
(its sweep / CHoCH-MSS / FVG-OB / retest / M15-confirmation facts come from the main chain, the three module votes from the modules).
Output: a Confidence Score /100 and a full plan (entry, stop, TP1, TP2, TP3, R:R, reasons, cancellation conditions).
Rules: incomplete or stale data = no verdict; score below ANALYSIS_MIN (80) = no final signal; real Order Flow is used only when real data is
supplied (the free feeds have none, so it is reported as unavailable and earns no points). Only CLOSED candles are read. Nothing is invented."""
from __future__ import annotations

import os

import indicators as ind
import structure as st

MIN_SCORE = 80.0
MIN_BARS = {"m15": 60, "h1": 210, "h4": 60}          # H1 needs 200+ closed bars for the SMA / EMA 200
CAP = {"trend": 20, "entry": 15, "liquidity": 15, "momentum": 15, "location": 10, "room": 15, "fib": 10}


def min_score():
    try:
        return float(os.environ.get("ANALYSIS_MIN") or MIN_SCORE)
    except ValueError:
        return MIN_SCORE


def enabled():
    return os.environ.get("ANALYSIS", "1") != "0"


# ---- indicators -----------------------------------------------------------------------------------------------------------------
def sma(values, n):
    return sum(values[-n:]) / n if len(values) >= n else None


def bollinger(bars, n=20, k=2.0):
    """(middle, upper, lower, %B, bandwidth%) of the closes, or None with fewer than n bars."""
    if len(bars) < n:
        return None
    c = [b["close"] for b in bars[-n:]]
    mid = sum(c) / n
    sd = (sum((x - mid) ** 2 for x in c) / n) ** 0.5
    up, lo = mid + k * sd, mid - k * sd
    pb = (bars[-1]["close"] - lo) / (up - lo) if up > lo else 0.5
    return mid, up, lo, pb, (up - lo) / mid * 100 if mid else 0.0


def ma_view(bars, want):
    """Trend by moving averages: price vs EMA50, EMA50 vs EMA200 (and SMA50 / SMA200 for the text). None values when too few bars."""
    c = [b["close"] for b in bars]
    e50 = ind.ema(c, 50)[-1] if len(c) >= 50 else None
    e200 = ind.ema(c, 200)[-1] if len(c) >= 200 else None
    s50, s200 = sma(c, 50), sma(c, 200)
    price = c[-1]
    side = "up" if want == "up" else "down"
    ok_price = None if e50 is None else (price > e50 if side == "up" else price < e50)
    ok_cross = None if (e50 is None or e200 is None) else (e50 > e200 if side == "up" else e50 < e200)
    return {"ema50": e50, "ema200": e200, "sma50": s50, "sma200": s200, "price_ok": ok_price, "cross_ok": ok_cross}


def sr_levels(bars, k=3, tol_atr=0.4):
    """Support / resistance from confirmed swing pivots, merged when closer than tol_atr x ATR. [{price, kind, touches}] sorted by price."""
    pv = st.pivots(bars, k)
    atr = st.atr_at(bars, len(bars)) or 0.0
    tol = atr * tol_atr
    levels = []
    for _, kind, price in pv:
        for lv in levels:
            if abs(lv["price"] - price) <= tol:
                lv["price"] = (lv["price"] * lv["touches"] + price) / (lv["touches"] + 1)
                lv["touches"] += 1
                break
        else:
            levels.append({"price": price, "kind": "resistance" if kind == "H" else "support", "touches": 1})
    return sorted(levels, key=lambda x: x["price"])


# ---- data completeness ----------------------------------------------------------------------------------------------------------
def completeness(m15, h1, h4, now_ts, fresh_bars=2):
    """List of problems; empty = the data is complete and fresh. Candles must already be closed ones, oldest first."""
    bad = []
    for name, bars in (("m15", m15), ("h1", h1), ("h4", h4)):
        if len(bars or []) < MIN_BARS[name]:
            bad.append(f"{name.upper()}: {len(bars or [])} closed candles, {MIN_BARS[name]} needed")
    if m15:
        age = now_ts - (int(m15[-1]["time"].timestamp()) + 900)
        if age > fresh_bars * 900:
            bad.append(f"M15 data is stale: the last closed candle ended {age // 60} min ago")
    return bad


# ---- the analysis ---------------------------------------------------------------------------------------------------------------
def _tp3(side, entry, tp2, risk, levels):
    """TP3 = the next support / resistance level beyond TP2 (up to 6R away); otherwise a 3R extension, labelled as such."""
    if side == "buy":
        beyond = [lv["price"] for lv in levels if lv["price"] > tp2 + 0.2 * risk and lv["price"] <= entry + 6 * risk]
        return (min(beyond), "next resistance") if beyond else (entry + 3 * risk, "3R extension")
    beyond = [lv["price"] for lv in levels if lv["price"] < tp2 - 0.2 * risk and lv["price"] >= entry - 6 * risk]
    return (max(beyond), "next support") if beyond else (entry - 3 * risk, "3R extension")


def analyze(market, side, plan, checks, votes, m15, h1, h4, now_ts, order_flow=None, digits=2):
    """plan: entry, sl, tp1, tp2, early_exit (from the main chain: structural stop, never tightened here). checks: the main chain's booleans
    (liquidity, bos_mss, fvg_ob, retest, confirm). votes: {'smc'|'scalper'|'pulse': vote with side}. Returns a dict:
    complete, problems, score (None when incomplete), parts, notes, plan (with TP3), reasons, cancel, ratings, final (bool), why_not."""
    out = {"market": market, "side": side, "complete": False, "problems": [], "score": None, "parts": {}, "notes": [], "plan": None,
           "reasons": [], "cancel": [], "ratings": {}, "final": False, "why_not": [], "indicators": {}}
    bad = completeness(m15, h1, h4, now_ts)
    if bad:
        out["problems"] = bad
        out["why_not"].append("data incomplete: " + "; ".join(bad))
        return out
    out["complete"] = True
    want = "up" if side == "buy" else "down"
    entry, sl = plan["entry"], plan["sl"]
    risk = abs(entry - sl)
    if risk <= 0:
        out["complete"] = False
        out["why_not"].append("entry and stop are the same price")
        return out
    sgn = 1 if side == "buy" else -1
    tp1 = plan.get("tp1") or entry + sgn * risk
    tp2 = plan["tp2"]
    parts = {k: 0.0 for k in CAP}
    reasons, notes = out["reasons"], out["notes"]

    # 1) higher-timeframe trend (20): moving averages and structure of H4 and H1
    for label, bars, pts in (("H4", h4, 5), ("H1", h1, 5)):
        ma = ma_view(bars, want)
        if ma["price_ok"] and ma["cross_ok"] is not False:
            parts["trend"] += pts * 0.6 if ma["cross_ok"] is None else pts * 0.6 + (pts * 0.4 if ma["cross_ok"] else 0)
            reasons.append(f"{label}: price {'above' if sgn > 0 else 'below'} EMA50" + (" and EMA50 " + ("above" if sgn > 0 else "below") + " EMA200" if ma["cross_ok"] else ""))
        else:
            notes.append(f"{label}: moving averages do not support a {side}")
        tr = st.current_trend(st.structure_events(bars, 3))
        if tr == want:
            parts["trend"] += pts
            reasons.append(f"{label} structure is {want}")
        else:
            notes.append(f"{label} structure is {tr or 'unclear'}")
        out["indicators"][label] = {k: (round(v, digits) if isinstance(v, float) else v) for k, v in ma.items()}

    # 2) M15 entry (15): structure and the closed confirmation candle (facts from the main chain)
    tr15 = st.current_trend(st.structure_events(m15, 2))
    if checks.get("bos_mss"):
        parts["entry"] += 8
        reasons.append("M15 CHoCH/MSS confirmed")
    elif tr15 == want:
        parts["entry"] += 4
        notes.append("M15 structure agrees but no CHoCH/MSS is confirmed")
    if checks.get("confirm"):
        parts["entry"] += 7
        reasons.append("closed M15 confirmation candle")

    # 3) liquidity and zone (15)
    if checks.get("liquidity"):
        parts["liquidity"] += 8
        reasons.append("liquidity sweep before the move")
    if checks.get("fvg_ob") and checks.get("retest"):
        parts["liquidity"] += 7
        reasons.append("FVG / order block retested")

    # 4) momentum (15): RSI, MACD on M15 and H1
    r = ind.rsi(m15)
    mc, mh = ind.macd(m15), ind.macd(h1)
    out["indicators"]["m15"] = {"rsi": None if r is None else round(r, 1)}
    if r is not None:
        ok = (40 <= r <= 70) if sgn > 0 else (30 <= r <= 60)
        if ok:
            parts["momentum"] += 5
            reasons.append(f"RSI {r:.0f} supports the {side} and is not exhausted")
        else:
            notes.append(f"RSI {r:.0f}: " + ("overbought" if r > 70 else "oversold" if r < 30 else "against the trade"))
    if mc:
        if (mc[2] > 0) == (sgn > 0) and (mc[2] - mc[3]) * sgn >= 0:
            parts["momentum"] += 5
            reasons.append("M15 MACD histogram agrees and is not fading")
        else:
            notes.append("M15 MACD does not support the trade")
    if mh:
        if (mh[2] > 0) == (sgn > 0):
            parts["momentum"] += 5
            reasons.append("H1 MACD agrees")
        else:
            notes.append("H1 MACD does not agree")

    # 5) location (10): Bollinger %B and premium / discount of the H1 range
    bb = bollinger(m15)
    if bb:
        pb = bb[3]
        if (pb < 0.85) if sgn > 0 else (pb > 0.15):
            parts["location"] += 5
            reasons.append(f"Bollinger %B {pb:.2f}: not chasing the band edge")
        else:
            notes.append(f"Bollinger %B {pb:.2f}: price is at the band edge")
        out["indicators"]["m15"]["bb_pctb"] = round(pb, 2)
    pd = st.premium_discount(h1, 3)
    if pd and ((pd["zone"] in ("discount", "equilibrium")) if sgn > 0 else (pd["zone"] in ("premium", "equilibrium"))):
        parts["location"] += 5
        reasons.append(f"entry from the {pd['zone']} of the H1 range")
    elif pd:
        notes.append(f"price is in the {pd['zone']} of the H1 range")

    # 6) room (15): the nearest opposing support / resistance on H1 and M15 against TP1 and TP2
    levels = sr_levels(h1, 3) + sr_levels(m15, 3)
    ahead = [lv["price"] for lv in levels if (lv["price"] > entry if sgn > 0 else lv["price"] < entry)]
    nearest = (min(ahead) if sgn > 0 else max(ahead)) if ahead else None
    if nearest is None or (nearest - entry) * sgn >= abs(tp2 - entry):
        parts["room"] = 15
        reasons.append("no opposing support / resistance before TP2")
    elif (nearest - entry) * sgn >= abs(tp1 - entry):
        parts["room"] = 7
        notes.append(f"an opposing level at {nearest:.{digits}f} sits between TP1 and TP2")
    else:
        notes.append(f"an opposing level at {nearest:.{digits}f} blocks the way before TP1")

    # 7) Fibonacci (10): entry inside the 50-79% golden zone of the latest M15 leg
    import selective
    inside, ftxt = selective.fib_check(m15, side, entry, 0.50, 0.79)
    if inside:
        parts["fib"] = 10
        reasons.append(ftxt)
    else:
        notes.append(ftxt)

    out["parts"] = {k: round(min(v, CAP[k]), 1) for k, v in parts.items()}
    score = round(sum(out["parts"].values()), 1)
    out["score"] = score

    # real order flow: only with real data; it is never estimated here
    if order_flow and order_flow.get("real") and order_flow.get("delta") is not None:
        if (order_flow["delta"] > 0) != (sgn > 0):
            out["why_not"].append("real order flow (delta) is against the trade")
        else:
            reasons.append("real order flow (delta) agrees")
        out["indicators"]["order_flow"] = "real data used"
    else:
        out["indicators"]["order_flow"] = "unavailable: this price feed has no real order-flow data (no points given, none invented)"

    # plan
    tp3, tp3_kind = _tp3(side, entry, tp2, risk, levels)
    rr2 = abs(tp2 - entry) / risk
    out["plan"] = {"side": side.upper(), "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp3_kind": tp3_kind,
                   "rr_tp1": abs(tp1 - entry) / risk, "rr_tp2": rr2, "rr_tp3": abs(tp3 - entry) / risk}
    ee = plan.get("early_exit")
    out["cancel"] = [f"a full M15 candle closes beyond {ee:.{digits}f}" if ee is not None else "a full M15 candle closes beyond the invalidation level",
                     "H1 structure turns against the trade (a CHoCH on H1)", "any of the three modules turns to the opposite direction",
                     "the signal validity time ends"]
    # ratings of the three strategies' signals (their own logic is untouched)
    for key, v in (votes or {}).items():
        agree = v.get("side") == side
        extra = {"smc": "H1 structure " + ("agrees" if st.current_trend(st.structure_events(h1, 3)) == want else "does not agree"),
                 "scalper": "sweep " + ("confirmed" if checks.get("liquidity") else "not confirmed"),
                 "pulse": "RSI/MACD indicators " + ("support the trade" if parts["momentum"] >= 10 else "are mixed")}[key]
        out["ratings"][key] = ("ALIGNED" if agree else "AGAINST") + f" ({extra})"

    import volatility_engine as ve
    if ve.enabled():                                               # ATR regime, abnormal candle, spread against the stop (Phase 3)
        vol = ve.assess(m15, plan.get("spread"), risk)
        out["indicators"]["volatility"] = vol["regime"] + (f" (ATR {vol['ratio']}x normal)" if vol["ratio"] else "")
        out["why_not"] += vol["problems"]
        notes += vol["notes"]
    if rr2 < 2.0:
        out["why_not"].append(f"R:R to TP2 is 1:{rr2:.1f}, below 1:2")
    if score < min_score():
        out["why_not"].append(f"analysis score {score:.0f}/100 is below the minimum {min_score():.0f}")
    out["final"] = not out["why_not"]
    return out


def format_report(a, digits=2):
    """English text block for the alert."""
    if not a["complete"]:
        return "PROFESSIONAL TRADE ANALYSIS: no verdict - " + "; ".join(a["why_not"] or a["problems"])
    p, ind_ = a["plan"], a["indicators"]
    n = lambda v: f"{v:.{digits}f}"
    lines = ["PROFESSIONAL TRADE ANALYSIS", f"Confidence Score: {a['score']:.0f}/100 (minimum {min_score():.0f}; counts conditions, not a win probability)",
             "Parts: " + " | ".join(f"{k} {v:g}/{CAP[k]}" for k, v in a["parts"].items()),
             f"Plan: {p['side']} | Entry {n(p['entry'])} | SL {n(p['sl'])} | TP1 {n(p['tp1'])} (1R) | TP2 {n(p['tp2'])} | TP3 {n(p['tp3'])} ({p['tp3_kind']})",
             f"Risk/Reward: TP1 1:{p['rr_tp1']:.1f} | TP2 1:{p['rr_tp2']:.1f} | TP3 1:{p['rr_tp3']:.1f}",
             "Why: " + "; ".join(a["reasons"][:8])]
    if a["notes"]:
        lines.append("Cautions: " + "; ".join(a["notes"][:5]))
    lines.append("Strategies: " + " | ".join(f"{k} {v}" for k, v in a["ratings"].items()))
    lines.append(f"Order flow: {ind_.get('order_flow')}")
    lines.append("Cancel if: " + "; ".join(a["cancel"]))
    return "\n".join(lines)
