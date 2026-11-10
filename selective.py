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

import ict_engine
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
    profile: str = "strict"         # "strict" (default) or "active" (see make_config)
    risk_percent: float = 0.25      # paper-trading risk per trade, % of the account (research setting)
    max_daily_losses: int = 3       # stop for the day after this many losses in a row (0 = off)
    max_daily_loss_pct: float = 1.5  # ... or after losing this % of the account in a UTC day
    max_daily_loss_r: float = None  # explicit R limit; default = max_daily_loss_pct / risk_percent
    ready_score: int = 86           # score (0-100) above 85 = eligible; 75-85 WATCH; below REJECT
    watch_score: int = 75
    allow_historical: bool = False  # backtests only
    min_bars: dict = field(default_factory=lambda: {"m5": 60, "m15": 60, "h1": 60, "h4": 40})


def daily_loss_limit_r(cfg):
    """Daily loss limit in R (0 = off). 1.5% of the account at 0.25% risk per trade = 6R."""
    if cfg.max_daily_loss_r is not None:
        return cfg.max_daily_loss_r
    return cfg.max_daily_loss_pct / cfg.risk_percent if cfg.risk_percent and cfg.max_daily_loss_pct else 0.0


def make_config(profile="strict", **overrides):
    """strict = the default, highly selective rules. active = a looser profile that gives more
    signals: lower confluence minimum (4/8), a longer retest window (5 M5 candles), zones may be
    older (192 M15 candles), FVGs up to 70% filled and a score of 80+ (strict needs 86+; a signal
    scoring 80-85 is labelled REDUCED CONFIDENCE). The hard rules (closed M5 candle, HTF
    alignment or a complete reversal sequence, retest, cost, locks, risk guard) are NOT relaxed.
    More signals means lower average quality; no measured result supports either profile yet."""
    if profile == "active":
        base = dict(profile="active", min_confluence=4, retest_window=5, max_zone_age=192,
                    fvg_max_fill=0.7, ready_score=80)
    elif profile == "strict":
        base = {}
    else:
        raise ValueError("PROFILE must be strict or active")
    base.update(overrides)
    return SelectiveConfig(**base)


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
    done = {}
    for c in candles:  # one candle per start time (the last listed wins), oldest first
        if int(c["time"].timestamp()) + tf_seconds <= now_ts:
            done[int(c["time"].timestamp())] = c
    return [done[k] for k in sorted(done)]


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
    if t4 == t1:
        return False, f"higher-timeframe trend is against a {side}: H4 {t4}, H1 {t1}"
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


# ===== Score out of 100 =====

SCORE_MAX = {"htf": 20, "liquidity": 15, "structure": 15, "displacement": 15, "zone": 15,
             "confirm": 10, "execution": 10}


def score_band(score, cfg=None):
    """READY (more than 85), WATCH (75 to 85) or REJECT. The score is NOT a probability of winning."""
    cfg = cfg or SelectiveConfig()
    return "READY" if score >= cfg.ready_score else "WATCH" if score >= cfg.watch_score else "REJECT"


def score_breakdown(htf_ok, mode, sweep_scope, mss, bos, zone_event, disp_ratio, zone_status, sr_hit,
                    confirm, strong, cost_ok, net_rr, cost_unverified=False):
    """Points per factor (max 20/15/15/15/15/10/10 = 100), each from a fixed, testable rule:
    htf: aligned 20, complete counter-trend reversal 10.
    liquidity: sweep of an external/session level 15, internal 12, swing 10; no sweep but a retested S/R 4.
    structure: MSS 15, BOS 10, only the zone's own break event 5.
    displacement: break/gap candle body vs ATR: >=1.5 -> 15, >=1.0 -> 11, >=0.8 -> 8.
    zone: fresh FVG/OB 15, partial 11, other valid 8, S/R only 6.
    confirm: closed M5 candle 6, closing beyond the previous candle 10.
    execution: net R:R >= 1.8 -> 10, >= 1.5 -> 7, otherwise 3; 0 if the cost check failed; at most 4
    when the spread is unverified."""
    htf = 20 if htf_ok else 10 if mode == "reversal" else 0
    liq = {"external": 15, "session": 15, "internal": 12, "swing": 10}.get(sweep_scope, 4 if sr_hit else 0)
    structure = 15 if mss else 10 if bos else 5 if zone_event else 0
    r = disp_ratio or 0.0
    disp = 15 if r >= 1.5 else 11 if r >= 1.0 else 8 if r >= 0.8 else 0
    zone = {"fresh": 15, "partial": 11}.get(zone_status, 8 if zone_status else (6 if sr_hit else 0))
    conf = 10 if (confirm and strong) else 6 if confirm else 0
    if not cost_ok:
        ex = 0
    else:
        ex = 10 if net_rr >= 1.8 else 7 if net_rr >= 1.5 else 3
        if cost_unverified:
            ex = min(ex, 4)
    parts = {"htf": htf, "liquidity": liq, "structure": structure, "displacement": disp,
             "zone": zone, "confirm": conf, "execution": ex}
    return parts, sum(parts.values())


# ===== The decision =====

def _zone_id(z):
    return f"{z['kind']}-{z['side']}-{round(z['low'], 6)}-{round(z['high'], 6)}-{z.get('idx')}"


def _ohlc(c):
    return f"O {c['open']:.5g} H {c['high']:.5g} L {c['low']:.5g} C {c['close']:.5g}"


def m1_timing(m1, side):
    """Timing refinement only: the last closed M1 candle. Never blocks and never creates a signal."""
    if not m1:
        return "M1 unavailable (timing refinement only)"
    c = m1[-1]
    agree = (c["close"] > c["open"]) == (side == "buy") and c["close"] != c["open"]
    return (f"last closed M1 {'agrees' if agree else 'is against'} ({_ohlc(c)}); "
            "M1 is timing only, never a signal source")


def evaluate(market, data, now_ts, cfg=None, spread=None, commission_price=None,
             status="LIVE", calendar="utc"):
    """data = {'h4','h1','m15','m5'} (+ optional 'd1', 'm1') candles oldest first. Returns:
    {'action': 'BUY'|'SELL'|'WAIT', 'reasons': [...], 'checks': {...}, 'setup': {...}|None,
     'status': ..., 'report': {...}}. Two ways to a signal:
      reversal     = liquidity sweep -> MSS with displacement -> valid FVG/OB -> retest -> closed M5 candle
      continuation = H4/H1 (and D1) alignment -> M15 structure -> pullback to a valid zone -> closed M5 candle"""
    cfg = cfg or SelectiveConfig()
    rep = {"mode": None, "htf": "not evaluated", "d1": "not evaluated", "liquidity": "not evaluated",
           "structure": "not evaluated", "zones": [], "retest": "not evaluated",
           "confirm": "not evaluated", "levels": None, "m1": "not evaluated", "premium_discount": None}
    res = {"market": market, "action": "WAIT", "reasons": [], "checks": {}, "setup": None,
           "status": status, "report": rep}
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
    d1 = closed_only(data.get("d1") or [], 86400, now_ts)
    m1 = closed_only(data.get("m1") or [], 60, now_ts)
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
    rep["confirm"] = f"closed M5 {_ohlc(last)} ({'bullish' if side == 'buy' else 'bearish'})"
    rep["m1"] = m1_timing(m1, side)

    ok_c, strong, why = confirmation(m5, side)
    checks = res["checks"]
    checks["confirm"] = ok_c
    if not ok_c:
        res["reasons"].append(why)
        return res
    if strong:
        rep["confirm"] += ", closed beyond the previous candle"

    want = "bull" if side == "buy" else "bear"
    role = "support" if side == "buy" else "resistance"
    fv_all = fvg_candidates(m15, cfg)
    ob_all = ob_candidates(m15, cfg)
    rev = ict_engine.reversal_sequence(m15, side, fv_all, ob_all, calendar)
    ev15 = st.structure_events(m15, 2)
    if rev["sweep"]:
        sw = rev["sweep"]
        rep["liquidity"] = (f"{sw['type']} {sw['level']} at {sw['price']:.5g} swept "
                            f"(extreme {sw['extreme']:.5g}, {sw['scope']})")
    else:
        rep["liquidity"] = "no confirmed sweep"
    if rev["event"]:
        e = rev["event"]
        rep["structure"] = f"{e['type']} {e['side']} with displacement = MSS"
    else:
        tr = st.current_trend(ev15)
        last_ev = ev15[-1] if ev15 else None
        rep["structure"] = (f"M15 trend {tr or 'unknown'}; last event "
                            f"{last_ev['type'] + ' ' + last_ev['side'] if last_ev else 'none'}")
    rep["reversal"] = f"stage {rev['stage']}/4" + (" complete" if rev["complete"] else
                                                   ": " + "; ".join(rev["reasons"][:2]))
    rep["zones"] = [f"{z['kind']} {z['side']} {z['low']:.5g}-{z['high']:.5g}"
                    f"{' valid' if z['valid'] else ' rejected: ' + ', '.join(z['reasons'])}"
                    for z in sorted(fv_all + ob_all, key=lambda z: -z["idx"])[:6] if z["side"] == want]

    # --- trend, D1 context
    ok_h, label = htf_alignment(h4, h1, m15, side, cfg)
    d1_trend, d1_label = ict_engine.d1_context(d1)
    rep["htf"] = label if ok_h else f"{label}"
    rep["d1"] = d1_label
    checks["htf"] = ok_h

    atr5 = st.atr_at(m5, len(m5))
    atr15 = st.atr_at(m15, len(m15))
    tested, notes = [], []

    def try_zone(z, name, tag):
        ok, why_not = retest(m5, z, side, cfg)
        if ok:
            tested.append(dict(z, name=name, tag=tag))
        else:
            notes.append(f"{name}: {why_not}")

    seq_ok, seq_notes = False, []
    if rev["complete"]:
        eq = rev["leg"]["eq"]
        rep["premium_discount"] = (f"leg {rev['leg']['low']:.5g}-{rev['leg']['high']:.5g}, "
                                   f"equilibrium {eq:.5g}")
        for z in rev["zones"]:
            right_half, half = ict_engine.in_correct_half(z, rev["leg"], side)
            if not right_half:
                seq_notes.append(f"{z['kind']} is in {half}: wrong side of the range for a {side}")
                continue
            try_zone(z, f"M15 {z['kind']} after sweep", "seq")
        seq_ok = any(t["tag"] == "seq" for t in tested)
    mode = "reversal" if seq_ok else "continuation"
    rep["mode"] = mode

    if mode == "continuation":
        if not ok_h:
            res["reasons"].append(label)
            if rev["stage"] >= 1 and not rev["complete"]:
                res["reasons"].append("no complete reversal sequence: " + "; ".join(rev["reasons"][:2]))
            elif rev["complete"]:
                res["reasons"].extend(seq_notes + notes[:2])
            return res
        if d1_trend and d1_trend != ("up" if side == "buy" else "down"):
            res["reasons"].append(f"{d1_label} opposes a continuation {side}")
            return res
        sr_all = [dict(z, tf=tf) for tf, b in (("H1", h1), ("H4", h4)) for z in sr_zones(b, cfg)]
        for z in sr_all:
            if z["role"] == role:
                try_zone(z, f"{z['tf']} {role} ({z['strength']}, {z['touches']} touches)", "sr")
        for f in fv_all:
            if f["side"] == want and f["valid"]:
                try_zone(f, f"M15 FVG {f['status']}", "fvg")
        for o in ob_all:
            if o["side"] == want and o["valid"]:
                try_zone(o, f"M15 order block ({o['event']})", "ob")
    else:
        label = ("reversal with the trend (" + label + ")" if ok_h
                 else "counter-trend reversal: complete sequence (" + label + ")")
        rep["htf"] = label
        sr_all = [dict(z, tf=tf) for tf, b in (("H1", h1), ("H4", h4)) for z in sr_zones(b, cfg)]
        for z in sr_all:
            if z["role"] == role:
                try_zone(z, f"{z['tf']} {role} ({z['strength']}, {z['touches']} touches)", "sr")

    sr_hit = [z for z in tested if z["kind"] == "SR"]
    fo_hit = [z for z in tested if z["kind"] in ("FVG", "OB")]
    sweeps = [s for s in ict_engine.find_liquidity_sweeps(m15, 12, calendar)
              if s["side"] == side and ict_engine.sweep_status(m15, s)[0] == "valid"]
    recent_break = any(e["side"] == want and len(m15) - 1 - e["idx"] <= 20 and
                       (e["type"] == "BOS" or e["mss"]) for e in ev15)
    checks.update({"sr": bool(sr_hit), "liquidity": bool(sweeps) or bool(rev["sweep"] and seq_ok),
                   "bos_mss": recent_break or bool(rev["event"] and seq_ok)
                   or any(z.get("event") for z in fo_hit),
                   "fvg_ob": bool(fo_hit), "retest": bool(tested)})
    rep["retest"] = ("; ".join(f"{z['name']} retested" for z in tested) if tested
                     else "no valid zone retested")
    if not tested:
        res["reasons"].append("no valid zone has been retested")
        res["reasons"].extend(notes[:4] + seq_notes[:2])
        return res

    # --- stop, target, costs
    primary = (fo_hit or sr_hit)[0]
    entry = last["close"]
    w = m5[-cfg.retest_window:]
    buf = cfg.sl_buffer_atr * atr5
    if side == "buy":
        sl = min(primary["low"], min(c["low"] for c in w)) - buf
    else:
        sl = max(primary["high"], max(c["high"] for c in w)) + buf
    invalidation = rev["sweep"]["extreme"] if mode == "reversal" else (
        primary["low"] if side == "buy" else primary["high"])
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

    hard_htf = mode == "continuation"       # a complete reversal sequence may go against H4/H1
    score = sum(1 for k in ("htf", "sr", "liquidity", "bos_mss", "fvg_ob", "retest", "confirm", "cost")
                if checks.get(k))
    res["score"] = score
    if hard_htf and not checks["htf"]:
        res["reasons"].append(label)
        return res
    if score < cfg.min_confluence:
        res["reasons"].append(f"confluence {score}/8 is under {cfg.min_confluence}")
        return res
    # --- score out of 100 (a ranking of setup quality, never a win probability)
    recent_ev = [e for e in ev15 if e["side"] == want and len(m15) - 1 - e["idx"] <= 20]
    cand_idx = [e["idx"] for e in recent_ev if e["displacement"]]
    if rev["event"] and seq_ok:
        cand_idx.append(rev["event"]["idx"])
    for z in fo_hit:
        cand_idx.append(z["idx"] - 1 if z["kind"] == "FVG" else z.get("break_idx", z.get("idx")))
    ratios = []
    for i in cand_idx:
        if i is not None and 1 <= i < len(m15):
            a = st.atr_at(m15, i)
            if a > 0:
                ratios.append(abs(m15[i]["close"] - m15[i]["open"]) / a)
    scope = None
    if rev["sweep"] and seq_ok:
        scope = rev["sweep"]["scope"]
    elif sweeps:
        scope = sweeps[-1]["scope"]
    top = fo_hit[0] if fo_hit else None
    net_rr = (cfg.rr * risk - cost) / (risk + cost)
    parts, score100 = score_breakdown(
        checks["htf"], mode, scope,
        mss=bool(rev["event"] and seq_ok) or any(e["mss"] for e in recent_ev),
        bos=any(e["type"] == "BOS" for e in recent_ev), zone_event=any(z.get("event") for z in fo_hit),
        disp_ratio=max(ratios, default=0.0), zone_status=top.get("status", "fresh") if top else None,
        sr_hit=bool(sr_hit), confirm=ok_c, strong=strong, cost_ok=ok_cost, net_rr=net_rr,
        cost_unverified=bool(note))
    band = score_band(score100, cfg)
    res.update(score100=score100, score_parts=parts, band=band)
    if band != "READY":
        res["reasons"].append(f"score {score100}/100 is {band}: a signal needs {cfg.ready_score}+ "
                              f"(WATCH {cfg.watch_score}-{cfg.ready_score - 1}). Parts: "
                              + ", ".join(f"{k} {v}/{SCORE_MAX[k]}" for k, v in parts.items()))
        return res
    uses = [_zone_id(z) for z in fo_hit]
    if mode == "reversal" and rev["event_id"]:
        uses.append(rev["event_id"])
    res["action"] = side.upper()
    res["setup"] = {"side": side, "entry": entry, "sl": sl, "tp": tp, "mode": mode,
                    "bar_time": int(last["time"].timestamp()), "cost": cost,
                    "cost_note": note, "strong_confirmation": strong, "uses": uses,
                    "zones": [z["name"] for z in tested], "htf": rep["htf"],
                    "invalidation": invalidation, "profile": cfg.profile,
                    "score100": score100, "score_parts": parts}
    rep["levels"] = (f"entry {entry:.5g}, invalidation {invalidation:.5g}, SL {sl:.5g}, TP {tp:.5g}, "
                     f"R:R 1:{cfg.rr:g} before costs")
    return res


def explain(res):
    """Plain-text report for a signal or a WAIT: every step and the exact reasons."""
    r = res["report"]
    head = (f"{res['action']} {res['market']} [{r['mode'] or 'no setup'}] data {res['status']}"
            + (f", confluence {res['score']}/8" if "score" in res else "")
            + (f", score {res['score100']}/100 {res['band']}" if "score100" in res else ""))
    lines = [head,
             f"HTF trend: {r['htf']}   |   {r['d1']}",
             f"Liquidity: {r['liquidity']}",
             f"Structure: {r['structure']}",
             f"Reversal sequence: {r.get('reversal', 'not evaluated')}"]
    if r["premium_discount"]:
        lines.append(f"Premium/discount: {r['premium_discount']}")
    lines.append("FVG/OB: " + ("; ".join(r["zones"]) if r["zones"] else "none"))
    lines += [f"Retest: {r['retest']}", f"M5 confirmation: {r['confirm']}", f"M1: {r['m1']}"]
    if r["levels"]:
        lines.append("Levels: " + r["levels"])
    if res["reasons"]:
        lines.append("Waiting because: " + " | ".join(res["reasons"]))
    return "\n".join(lines)


def format_signal(market, res, digits=2):
    s = res["setup"]
    c = res["checks"]
    mark = lambda k: "yes" if c.get(k) else "no"  # noqa: E731
    tag = ", ACTIVE profile (looser filter)" if s.get("profile") == "active" else ""
    if s.get("score100", 100) < 86:
        tag += ", REDUCED CONFIDENCE (score below 86)"
    lines = [f"SELECTIVE SIGNAL: {s['side'].upper()} {market}  [{s['mode']}, data {res['status']}{tag}]",
             f"Entry: {s['entry']:.{digits}f}  Invalidation: {s['invalidation']:.{digits}f}  "
             f"SL: {s['sl']:.{digits}f}  TP: {s['tp']:.{digits}f}  (R:R 1:2 before costs)",
             f"Score {res['score100']}/100 ({res['band']}; a quality ranking, NOT a win probability) | "
             + ", ".join(f"{k} {v}/{SCORE_MAX[k]}" for k, v in res["score_parts"].items()),
             f"Confluence {res['score']}/8: HTF {mark('htf')}, S/R {mark('sr')}, liquidity {mark('liquidity')}, "
             f"BOS/MSS {mark('bos_mss')}, FVG/OB {mark('fvg_ob')}, retest {mark('retest')}, "
             f"M5 close {mark('confirm')}{' (strong)' if s['strong_confirmation'] else ''}, cost {mark('cost')}",
             "Zones: " + "; ".join(s["zones"]),
             f"Cost assumed: {s['cost']:.4g} price units" + (f" ({s['cost_note']})" if s["cost_note"] else ""),
             explain(res),
             "Signal only. Place the order yourself. This is not a promised win rate."]
    return "\n".join(lines)


AR_NAMES = {"XAUUSD": "الذهب", "NAS100": "ناسداك", "US100": "ناسداك", "US30": "داو جونز",
            "BTCUSD": "البتكوين"}


_AR_WORDS = (("counter-trend reversal: complete sequence", "انعكاس ضد الاتجاه بتسلسل كامل"),
             ("reversal with the trend", "انعكاس مع الاتجاه"), ("confirmed reversal", "انعكاس مؤكد"),
             ("H4 and H1 up", "H4 و H1 صاعدان"), ("H4 and H1 down", "H4 و H1 هابطان"),
             ("D1 unavailable", "D1 غير متوفر"), ("D1 up", "D1 صاعد"), ("D1 down", "D1 هابط"),
             ("no confirmed sweep", "لا يوجد sweep مؤكد"), ("swept", "تم سحبها"),
             ("M15 trend up", "اتجاه M15 صاعد"), ("M15 trend down", "اتجاه M15 هابط"),
             ("last event", "آخر حدث"), ("bullish", "صاعدة"), ("bearish", "هابطة"), ("closed M5", "M5 مغلقة"),
             ("closed beyond the previous candle", "أغلقت خلف قمة/قاع الشمعة السابقة"),
             ("with displacement = MSS", "مع displacement = MSS"), ("bull", "صاعد"), ("bear", "هابط"),
             ("fresh", "جديدة"), ("partial", "مستهلكة جزئياً"), ("order block", "Order Block"),
             ("external", "خارجي"), ("internal", "داخلي"), ("session", "جلسة"), ("swing", "swing"),
             ("extreme", "الطرف"), ("support", "دعم"), ("resistance", "مقاومة"),
             ("strong", "قوية"), ("weak", "ضعيفة"), ("touches", "لمسات"), ("MSS", "MSS"))


def _ar(text):
    for en, ar in _AR_WORDS:
        text = text.replace(en, ar)
    return text


def format_signal_ar(market, res, digits=2):
    """Arabic entry alert (terms such as FVG, OB, MSS stay in English as traders use them)."""
    s, c, r = res["setup"], res["checks"], res["report"]
    yn = lambda k: "نعم" if c.get(k) else "لا"  # noqa: E731
    side = "شراء" if s["side"] == "buy" else "بيع"
    mode = "انعكاس بعد سحب سيولة" if s["mode"] == "reversal" else "استمرار الاتجاه"
    name = AR_NAMES.get(market, market)
    cost = f"{s['cost']:.4g}"
    if s["cost_note"]:
        cost += " (غير موثّقة: السبريد غير معروف، تحقق منه في MT5)"
    lines = [f"إشارة دخول: {side} {name} ({market})",
             f"النوع: {mode} | حالة البيانات: {res['status']}"
             + (" | ملف نشط (فلتر أخف)" if s.get("profile") == "active" else "")
             + (" | ثقة أقل (التقييم أقل من 86)" if s.get("score100", 100) < 86 else ""),
             f"الدخول: {s['entry']:.{digits}f}",
             f"الإبطال: {s['invalidation']:.{digits}f}",
             f"وقف الخسارة: {s['sl']:.{digits}f}",
             f"الهدف: {s['tp']:.{digits}f}  (نسبة 1:2 قبل التكاليف)",
             f"التقييم {res['score100']}/100 (ترتيب لجودة الإعداد وليس احتمال ربح)",
             f"التوافق {res['score']}/8: الاتجاه {yn('htf')}، دعم/مقاومة {yn('sr')}، سيولة {yn('liquidity')}، "
             f"BOS/MSS {yn('bos_mss')}، FVG/OB {yn('fvg_ob')}، إعادة اختبار {yn('retest')}، "
             f"شمعة M5 مغلقة {yn('confirm')}{' (قوية)' if s['strong_confirmation'] else ''}، التكلفة {yn('cost')}",
             f"المناطق: {_ar('; '.join(s['zones']))}",
             f"اتجاه الفريمات الكبيرة: {_ar(r['htf'])} | {_ar(r['d1'])}",
             f"السيولة: {_ar(r['liquidity'])}",
             f"الهيكل: {_ar(r['structure'])}",
             f"شمعة التأكيد M5: {_ar(r['confirm'])}",
             f"التكلفة المفترضة: {cost}",
             "هذه إشارة فقط: نفّذ الصفقة بنفسك في MT5 بعد مقارنة السعر. لا توجد نسبة نجاح مضمونة."]
    return "\n".join(lines)


def process(market, data, now_ts, store, cfg=None, spread=None, commission_price=None,
            status="LIVE", calendar="utc"):
    """evaluate() plus the one-active-signal-per-market lock. The lock is only taken when a
    signal is really issued."""
    res = evaluate(market, data, now_ts, cfg, spread, commission_price, status, calendar)
    cfg = cfg or SelectiveConfig()
    if res["action"] != "WAIT":
        stop = store.halted(now_ts, cfg.max_daily_losses, daily_loss_limit_r(cfg))
        ok, why = (False, stop) if stop else store.open(market, res["setup"], now_ts)
        if not ok:
            res["action"], res["setup"] = "WAIT", None
            res["reasons"].append(why)
        else:
            res["id"] = store.active(market)["id"]
    return res
