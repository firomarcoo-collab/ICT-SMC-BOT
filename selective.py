"""Highly selective signal engine (signals only, no orders).

Candles are lists of {'time' (datetime), 'open', 'high', 'low', 'close'}, OLDEST FIRST.
Roles: H4 + H1 give trend and support/resistance, M15 finds the setup (FVG / order block),
M5 gives the closing confirmation. M1 is never used.

A signal needs ALL of the hard requirements and at least `min_confluence` of the 8 checks:
  hard: 1 higher-timeframe alignment, 6 a real retest of a valid zone, 7 a fully CLOSED M5
        confirmation candle, 8 acceptable spread / cost / risk-reward, data status LIVE.
  soft: 2 support/resistance context, 3 liquidity sweep, 4 BOS/MSS, 5 valid FVG or order block.
Anything else returns WAIT with the reasons. Unfinished candles are dropped before any
analysis, so a candle that is still forming can never create a signal.

Nothing here claims a win rate. Whether the rules are profitable is a question for
backtest.py, out-of-sample tests and forward paper trading.
"""
from dataclasses import dataclass, field

import structure as st

LIVE_SOURCES = ("binance", "yahoo")
TF_SECONDS = {"m5": 300, "m15": 900, "h1": 3600, "h4": 14400}


@dataclass
class SelectiveConfig:
    min_confluence: int = 5
    htf_k: int = 3                  # swing order for H4 / H1 structure
    reversal_max_age: int = 20      # H1 bars an MSS may be old and still count as a reversal
    sr_k: int = 3
    sr_tol_atr: float = 0.5         # pivots this close (in ATR) form one zone
    sr_decisive_atr: float = 0.25   # a CLOSE this far beyond a zone is a real break
    strong_touches: int = 3
    retest_window: int = 3          # M5 bars (including the confirmation bar)
    approach_lookback: int = 24     # M5 bars before the window where price must come from the right side
    fvg_max_fill: float = 0.5
    max_zone_age: int = 96          # M15 bars
    min_zone_atr: float = 0.3       # order-block candle size
    sl_buffer_atr: float = 0.15     # of M5 ATR
    max_risk_atr: float = 3.0       # stop wider than this many M15 ATR = WAIT
    rr: float = 2.0
    min_net_rr: float = 1.5         # after spread, commission and slippage
    max_cost_frac: float = 0.15     # cost / stop distance
    slippage: float = 0.0           # price units, added to the cost
    allow_unknown_cost: bool = False
    max_signal_age: int = 180       # seconds after the confirmation candle closed
    allow_historical: bool = False  # backtests only
    min_bars: dict = field(default_factory=lambda: {"m5": 60, "m15": 60, "h1": 60, "h4": 40})


# ===== Data status and closed-candle handling =====

def data_status(source, candles, tf_seconds, now_ts, connected=True, historical=False):
    """LIVE, HISTORICAL, SIMULATED, STALE or DISCONNECTED. Only LIVE can create a live signal."""
    if not connected or not candles:
        return "DISCONNECTED"
    if source not in LIVE_SOURCES:
        return "SIMULATED"
    if historical:
        return "HISTORICAL"
    newest = int(candles[-1]["time"].timestamp())
    if now_ts - newest > 3 * tf_seconds + 300:
        return "STALE"
    return "LIVE"


def closed_only(candles, tf_seconds, now_ts):
    """Drop every candle that has not finished yet (its end is after now_ts)."""
    return [c for c in candles if int(c["time"].timestamp()) + tf_seconds <= now_ts]


# ===== OHLC helpers =====

def classify_break(bar, level, side):
    """'close' when the candle CLOSED beyond the level, 'wick' when only the wick crossed it, else None.
    side 'up' tests a break above the level, 'down' below."""
    if side == "up":
        if bar["close"] > level:
            return "close"
        return "wick" if bar["high"] > level else None
    if bar["close"] < level:
        return "close"
    return "wick" if bar["low"] < level else None


def confirmation(m5, side):
    """Checks the LAST candle of `m5` (must already be closed). Returns (ok, strong, reason).
    strong = it also closed beyond the previous candle's high (buy) / low (sell)."""
    if len(m5) < 2:
        return False, False, "not enough M5 candles"
    c, p = m5[-1], m5[-2]
    if side == "buy":
        if not c["close"] > c["open"]:
            return False, False, "last closed M5 candle is not bullish"
        return True, c["close"] > p["high"], ""
    if not c["close"] < c["open"]:
        return False, False, "last closed M5 candle is not bearish"
    return True, c["close"] < p["low"], ""


# ===== Support / resistance (H1 and H4) =====

def sr_zones(bars, cfg=None):
    """Zones from clusters of confirmed swing points (at least 2 touches).
    Role follows closes: a CLOSE decisively beyond a resistance turns it into support (and the
    reverse); wicks do not change the role. Returns dicts with low, high, role, touches,
    strength ('strong' = enough touches), flips, atr."""
    cfg = cfg or SelectiveConfig()
    k = cfg.sr_k
    pv = st.pivots(bars, k)
    atr = st.atr_at(bars, len(bars))
    if len(pv) < 2 or atr <= 0:
        return []
    pts = sorted(pv, key=lambda p: p[2])
    clusters, cur = [], [pts[0]]
    for p in pts[1:]:
        if p[2] - cur[0][2] <= cfg.sr_tol_atr * atr:
            cur.append(p)
        else:
            clusters.append(cur)
            cur = [p]
    clusters.append(cur)
    zones = []
    for cl in clusters:
        if len(cl) < 2:
            continue
        lo, hi = min(p[2] for p in cl), max(p[2] for p in cl)
        if hi - lo < 0.2 * atr:
            mid = (hi + lo) / 2
            lo, hi = mid - 0.1 * atr, mid + 0.1 * atr
        first = min(cl, key=lambda p: p[0])
        last_idx = max(p[0] for p in cl)
        role = "resistance" if first[1] == "H" else "support"
        flips = reactions = 0
        prev_hit = False
        for x in range(first[0] + 1, len(bars)):
            c = bars[x]
            d = cfg.sr_decisive_atr * (st.atr_at(bars, x) or atr)
            if role == "resistance" and c["close"] > hi + d:
                role, flips, prev_hit = "support", flips + 1, False
                continue
            if role == "support" and c["close"] < lo - d:
                role, flips, prev_hit = "resistance", flips + 1, False
                continue
            if x > last_idx + k:
                overlap = c["low"] <= hi and c["high"] >= lo
                rejected = overlap and ((role == "resistance" and c["close"] < lo)
                                        or (role == "support" and c["close"] > hi))
                if rejected and not prev_hit:
                    reactions += 1
                prev_hit = rejected
        touches = len(cl) + reactions
        zones.append({"kind": "SR", "low": lo, "high": hi, "role": role, "touches": touches,
                      "strength": "strong" if touches >= cfg.strong_touches else "weak",
                      "flips": flips, "atr": atr, "idx": last_idx})
    return zones


# ===== FVG validation (M15) =====

def fvg_candidates(bars, cfg=None):
    """Every FVG with a verdict: {'side','low','high','idx','status','fill','valid','reasons','atr'}.
    status: fresh / partial / mitigated (fully consumed). Valid = three-candle gap with a
    displacement middle candle, in line with the structure, not more than cfg.fvg_max_fill filled."""
    cfg = cfg or SelectiveConfig()
    events = st.structure_events(bars, 2)
    out = []
    atr_now = st.atr_at(bars, len(bars))
    for g in st.fvgs(bars, include_filled=True):
        i = g["idx"] - 2
        a, m, c = bars[i], bars[i + 1], bars[i + 2]
        bull = g["side"] == "bull"
        reasons = []
        formed = (c["low"] > a["high"]) if bull else (c["high"] < a["low"])
        if not formed:
            reasons.append("not a real three-candle gap")
        if not st.is_displacement(bars, i + 1):
            reasons.append("middle candle is not a displacement")
        if (m["close"] > m["open"]) != bull:
            reasons.append("middle candle points the wrong way")
        want = "bull" if bull else "bear"
        near_break = any(e["side"] == want and i <= e["idx"] <= i + 3 for e in events)
        past = [e for e in events if e["idx"] <= i + 2]
        trend_then = st.current_trend(past)
        if not near_break and trend_then != ("up" if bull else "down"):
            reasons.append("no structure break and the trend then was against it")
        height = g["high"] - g["low"]
        later = bars[g["idx"] + 1:]
        if bull:
            deepest = min((x["low"] for x in later), default=g["high"])
            fill = max(0.0, min(1.0, (g["high"] - deepest) / height)) if height else 1.0
            closed_through = any(x["close"] < g["low"] for x in later)
        else:
            deepest = max((x["high"] for x in later), default=g["low"])
            fill = max(0.0, min(1.0, (deepest - g["low"]) / height)) if height else 1.0
            closed_through = any(x["close"] > g["high"] for x in later)
        status = "fresh" if fill == 0 else ("mitigated" if fill >= 1 or closed_through else "partial")
        if status == "mitigated":
            reasons.append("already fully consumed")
        elif fill > cfg.fvg_max_fill:
            reasons.append(f"{fill:.0%} filled, more than {cfg.fvg_max_fill:.0%}")
        if len(bars) - g["idx"] > cfg.max_zone_age:
            reasons.append("too old")
        out.append({"kind": "FVG", "side": g["side"], "low": g["low"], "high": g["high"],
                    "idx": g["idx"], "status": status, "fill": fill, "valid": not reasons,
                    "reasons": reasons, "atr": atr_now})
    return out


# ===== Order block validation (M15) =====

def ob_candidates(bars, cfg=None):
    """Order blocks with a verdict. Valid = origin of a displacement that made a BOS or an MSS,
    candle big enough, never touched since (fresh), never closed through, not too old."""
    cfg = cfg or SelectiveConfig()
    events = st.structure_events(bars, 2)
    atr_now = st.atr_at(bars, len(bars))
    out = []
    for ob in st.order_blocks(bars, events):
        if ob["kind"] != "OB":
            continue  # breakers are not traded by this engine
        reasons = []
        if ob["event"] == "CHoCH" and not ob["mss"]:
            reasons.append("CHoCH without displacement (no MSS)")
        size = ob["high"] - ob["low"]
        if size < cfg.min_zone_atr * (st.atr_at(bars, ob["idx"]) or atr_now):
            reasons.append("block candle too small")
        if ob["status"] == "broken":
            reasons.append("price closed through it")
        elif ob["status"] == "mitigated":
            reasons.append("already mitigated (touched)")
        if len(bars) - ob["break_idx"] > cfg.max_zone_age:
            reasons.append("too old")
        out.append(dict(ob, valid=not reasons, reasons=reasons, atr=atr_now))
    return out


# ===== Retest, trend, cost =====

def retest(m5, zone, side, cfg):
    """Did the last `retest_window` closed M5 candles really test the zone (and not break it)?
    Returns (ok, reason). Wicks into/through the zone are fine, a decisive CLOSE beyond it is not."""
    w = m5[-cfg.retest_window:]
    lo, hi = zone["low"], zone["high"]
    dec = cfg.sr_decisive_atr * (zone.get("atr") or 0)
    if side == "buy":
        if any(classify_break(c, lo - dec, "down") == "close" for c in w):
            return False, "closed decisively below the zone"
        touched = [c for c in w if c["low"] <= hi and c["high"] >= lo]
        before = m5[-(cfg.approach_lookback + cfg.retest_window):-cfg.retest_window]
        came = any(c["low"] > hi for c in before)
        if w[-1]["close"] < lo:
            return False, "confirmation candle closed under the zone"
    else:
        if any(classify_break(c, hi + dec, "up") == "close" for c in w):
            return False, "closed decisively above the zone"
        touched = [c for c in w if c["low"] <= hi and c["high"] >= lo]
        before = m5[-(cfg.approach_lookback + cfg.retest_window):-cfg.retest_window]
        came = any(c["high"] < lo for c in before)
        if w[-1]["close"] > hi:
            return False, "confirmation candle closed above the zone"
    if not touched:
        return False, "price has not come back to the zone"
    if not came:
        return False, "price did not approach the zone from the entry side"
    return True, ""


def htf_alignment(h4, h1, m15, side, cfg):
    """(ok, label). H4 and H1 must agree with the trade and M15 must be in the same direction.
    A trade against H4 is allowed only after a recent H1 MSS in its direction (confirmed reversal)."""
    want = "up" if side == "buy" else "down"
    e4, e1, e15 = (st.structure_events(h4, cfg.htf_k), st.structure_events(h1, cfg.htf_k),
                   st.structure_events(m15, 2))
    t4, t1, t15 = st.current_trend(e4), st.current_trend(e1), st.current_trend(e15)
    if t4 is None or t1 is None:
        return False, "higher-timeframe structure unknown"
    if t15 != want:
        return False, f"M15 structure is {t15 or 'unknown'}, against a {side}"
    if t4 == want and t1 == want:
        return True, f"H4 and H1 {want}"
    if t1 == want and t4 != want:
        last = e1[-1]
        if last["mss"] and len(h1) - 1 - last["idx"] <= cfg.reversal_max_age:
            return True, "confirmed reversal (H1 MSS, M15 agrees)"
        return False, f"H4 is {t4}; the H1 reversal is not confirmed by an MSS"
    return False, f"higher-timeframe conflict: H4 {t4}, H1 {t1}"


def cost_check(risk, spread, commission_price, cfg):
    """(ok, cost, note). cost = spread + commission + slippage in price units."""
    if spread is None and not cfg.allow_unknown_cost:
        return False, None, "spread unknown (set SPREAD_<SYMBOL> or fill markets.json)"
    cost = (spread or 0.0) + (commission_price or 0.0) + cfg.slippage
    if risk <= 0:
        return False, cost, "no stop distance"
    if cost / risk > cfg.max_cost_frac:
        return False, cost, f"cost {cost:.4g} is {cost / risk:.0%} of the stop (limit {cfg.max_cost_frac:.0%})"
    net = (cfg.rr * risk - cost) / (risk + cost)
    if net < cfg.min_net_rr:
        return False, cost, f"net R:R {net:.2f} after costs is under {cfg.min_net_rr}"
    note = "" if spread is not None else "COST UNVERIFIED"
    return True, cost, note


# ===== The decision =====

def evaluate(market, data, now_ts, cfg=None, spread=None, commission_price=None,
             status="LIVE", calendar="utc"):
    """data = {'h4','h1','m15','m5'} candles oldest first. Returns a dict:
    {'action': 'BUY'|'SELL'|'WAIT', 'reasons': [...], 'checks': {...}, 'setup': {...}|None, 'status': ...}"""
    cfg = cfg or SelectiveConfig()
    res = {"market": market, "action": "WAIT", "reasons": [], "checks": {}, "setup": None,
           "status": status}
    if status != "LIVE" and not (status == "HISTORICAL" and cfg.allow_historical):
        res["reasons"].append(f"data status {status}: no signal")
        return res
    bars = {}
    for tf, sec in TF_SECONDS.items():
        bars[tf] = closed_only(data.get(tf) or [], sec, now_ts)
        if len(bars[tf]) < cfg.min_bars[tf]:
            res["reasons"].append(f"not enough closed {tf.upper()} candles ({len(bars[tf])})")
    if res["reasons"]:
        return res
    m5, m15, h1, h4 = bars["m5"], bars["m15"], bars["h1"], bars["h4"]
    last = m5[-1]
    close_time = int(last["time"].timestamp()) + TF_SECONDS["m5"]
    if now_ts - close_time > cfg.max_signal_age:
        res["reasons"].append("the confirmation candle is too old")
        return res
    side = "buy" if last["close"] > last["open"] else "sell" if last["close"] < last["open"] else None
    if side is None:
        res["reasons"].append("last closed M5 candle is a doji")
        return res

    ok_c, strong, why = confirmation(m5, side)
    checks = res["checks"]
    checks["confirm"] = ok_c
    if not ok_c:
        res["reasons"].append(why)
        return res
    ok_h, label = htf_alignment(h4, h1, m15, side, cfg)
    checks["htf"] = ok_h
    if not ok_h:
        res["reasons"].append(label)
        return res

    want = "bull" if side == "buy" else "bear"
    role = "support" if side == "buy" else "resistance"
    atr5 = st.atr_at(m5, len(m5))
    atr15 = st.atr_at(m15, len(m15))
    tested, notes = [], []

    def try_zone(z, name):
        ok, why_not = retest(m5, z, side, cfg)
        if ok:
            tested.append(dict(z, name=name))
        else:
            notes.append(f"{name}: {why_not}")

    sr_all = [dict(z, tf=tf) for tf, b in (("H1", h1), ("H4", h4)) for z in sr_zones(b, cfg)]
    for z in sr_all:
        if z["role"] == role:
            try_zone(z, f"{z['tf']} {role} ({z['strength']}, {z['touches']} touches)")
    fv = [f for f in fvg_candidates(m15, cfg) if f["side"] == want and f["valid"]]
    ob = [o for o in ob_candidates(m15, cfg) if o["side"] == want and o["valid"]]
    for f in fv:
        try_zone(f, f"M15 FVG {f['status']}")
    for o in ob:
        try_zone(o, f"M15 order block ({o['event']})")
    sr_hit = [z for z in tested if z["kind"] == "SR"]
    fo_hit = [z for z in tested if z["kind"] in ("FVG", "OB")]

    sweeps = [s for s in st.find_sweeps(m15, recent=12, calendar=calendar) if s["side"] == side]
    recent_break = any(e["side"] == want and len(m15) - 1 - e["idx"] <= 20 and
                       (e["type"] == "BOS" or e["mss"]) for e in st.structure_events(m15, 2))
    checks.update({"sr": bool(sr_hit), "liquidity": bool(sweeps),
                   "bos_mss": recent_break or any(z.get("event") for z in fo_hit),
                   "fvg_ob": bool(fo_hit), "retest": bool(tested)})
    if not tested:
        res["reasons"].append("no valid zone has been retested")
        res["reasons"].extend(notes[:4])
        return res

    # stop, target, costs
    primary = (fo_hit or sr_hit)[0]
    entry = last["close"]
    w = m5[-cfg.retest_window:]
    buf = cfg.sl_buffer_atr * atr5
    if side == "buy":
        sl = min(primary["low"], min(c["low"] for c in w)) - buf
    else:
        sl = max(primary["high"], max(c["high"] for c in w)) + buf
    risk = abs(entry - sl)
    if atr15 and risk > cfg.max_risk_atr * atr15:
        res["reasons"].append(f"stop too wide ({risk / atr15:.1f} M15 ATR)")
        checks["cost"] = False
        return res
    ok_cost, cost, note = cost_check(risk, spread, commission_price, cfg)
    checks["cost"] = ok_cost
    if not ok_cost:
        res["reasons"].append(note)
        return res
    tp = entry + cfg.rr * risk if side == "buy" else entry - cfg.rr * risk
    for z in sr_all:  # a strong opposing zone between entry and target blocks the trade
        if z["strength"] == "strong" and z["role"] != role:
            in_path = (entry < z["low"] < tp) if side == "buy" else (tp < z["high"] < entry)
            if in_path:
                res["reasons"].append(f"target blocked by a strong {z['tf']} {z['role']}")
                return res

    score = sum(1 for k in ("htf", "sr", "liquidity", "bos_mss", "fvg_ob", "retest", "confirm", "cost")
                if checks.get(k))
    res["score"] = score
    if score < cfg.min_confluence:
        res["reasons"].append(f"confluence {score}/8 is under {cfg.min_confluence}")
        return res
    res["action"] = side.upper()
    res["setup"] = {"side": side, "entry": entry, "sl": sl, "tp": tp,
                    "bar_time": int(last["time"].timestamp()), "cost": cost,
                    "cost_note": note, "strong_confirmation": strong,
                    "zones": [z["name"] for z in tested], "htf": label}
    return res


def format_signal(market, res, digits=2):
    s = res["setup"]
    c = res["checks"]
    mark = lambda k: "yes" if c.get(k) else "no"  # noqa: E731
    lines = [f"SELECTIVE SIGNAL: {s['side'].upper()} {market}  [data {res['status']}]",
             f"Entry: {s['entry']:.{digits}f}  SL: {s['sl']:.{digits}f}  TP: {s['tp']:.{digits}f}  (R:R 1:2 before costs)",
             f"Confluence {res['score']}/8: HTF {mark('htf')}, S/R {mark('sr')}, liquidity {mark('liquidity')}, "
             f"BOS/MSS {mark('bos_mss')}, FVG/OB {mark('fvg_ob')}, retest {mark('retest')}, "
             f"M5 close {mark('confirm')}{' (strong)' if s['strong_confirmation'] else ''}, cost {mark('cost')}",
             f"Trend: {s['htf']}", "Zones: " + "; ".join(s["zones"]),
             f"Cost assumed: {s['cost']:.4g} price units" + (f" ({s['cost_note']})" if s["cost_note"] else ""),
             "Signal only. Place the order yourself. This is not a promised win rate."]
    return "\n".join(lines)


def process(market, data, now_ts, store, cfg=None, spread=None, commission_price=None,
            status="LIVE", calendar="utc"):
    """evaluate() plus the one-active-signal-per-market lock. The lock is only taken when a
    signal is really issued."""
    res = evaluate(market, data, now_ts, cfg, spread, commission_price, status, calendar)
    if res["action"] != "WAIT":
        ok, why = store.open(market, res["setup"], now_ts)
        if not ok:
            res["action"], res["setup"] = "WAIT", None
            res["reasons"].append(why)
        else:
            res["id"] = store.active(market)["id"]
    return res
