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
from datetime import datetime, timezone

import ict_engine
import indicators
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
    profile: str = "strict"         # "strict" (default), "active" or "loose" (see make_config)
    forex: bool = False             # forex profile: M15 closed candle is the primary confirmation, own 0-100 score
    single_active: bool = False     # at most one open signal per symbol, whatever MAX_ACTIVE says
    require_fib: bool = False       # the entry must sit in the 62-79% golden zone of the M15 leg
    require_sweep: bool = False     # an entry needs a valid liquidity sweep (scalp profile)
    ltf_bias: bool = False          # scalp: direction from M5 + M1 structure instead of H4 + H1 alignment
    strong_body_frac: float = None  # also count a closed M5 candle with body >= this share of its range as strong
    risk_percent: float = 0.25      # paper-trading risk per trade, % of the account (research setting)
    max_daily_losses: int = 3       # stop for the day after this many losses in a row (0 = off)
    max_daily_loss_pct: float = 1.5  # ... or after losing this % of the account in a UTC day
    max_daily_loss_r: float = None  # explicit R limit; default = max_daily_loss_pct / risk_percent
    ready_score: int = 86           # score (0-100) above 85 = eligible; 75-85 WATCH; below REJECT
    watch_score: int = 75
    allow_historical: bool = False  # backtests only
    spread_margin: float = 1.0      # the spread is multiplied by this in the cost check (1.25 = 25% safety margin)
    require_mss_fvg: bool = False   # an entry needs an MSS/CHoCH with displacement AND a valid FVG (not just a zone touch)
    min_displacement: float = 1.0   # that displacement candle body must be at least this many M15 ATR
    counter_trend_min_score: int = 0    # a COUNTER-TREND signal needs this score (0 = no extra rule) + a strong confirm
    htf_mode: str = "default"       # "h1m15" (scalp: H1 + M15 trend), "d1h4" (swing: D1 + H4 trend), else H4/H1/M15 rules
    struct_tf: str = "m15"          # timeframe of the sweep / MSS / FVG-OB analysis ("h1" for swing)
    confirm_tf: str = "m5"          # closed candle that triggers the entry ("m15" for swing; forex always uses M15)
    volume_filter: bool = False     # a displacement needs volume >= vol_mult x avg AND body > body_mult x avg AND MSS + FVG
    vol_mult: float = indicators.VOLUME_MULT
    body_mult: float = indicators.BODY_MULT
    volume_required: bool = False   # True: a feed without volume data cannot confirm a displacement
    require_m15_confirm: bool = False   # the last closed M15 candle must agree with the trade (on top of the M5 trigger)
    h4_zones: bool = False          # swing: a fresh H4 FVG/OB also counts as the zone of interest (see h4_zone_fallback)
    strict_sequence: bool = False   # sweep -> MSS (displacement >= min_displacement ATR) -> retest of a FRESH FVG/OB; never counter-trend
    late_entry_frac: float = 0.0    # cancel when price already travelled this share of the way to TP1 before the retest (0 = off)
    m1_confirm: bool = False        # scalp: the last closed M1 and M3 candles must agree with the trade
    blackout: tuple = ()            # UTC (start_hour, end_hour) windows where no new signal is made (swing: before the daily close)
    killzones: tuple = ()           # UTC (start_hour, end_hour) windows; empty = always. Signals only inside them
    min_bars: dict = field(default_factory=lambda: {"m5": 60, "m15": 60, "h1": 60, "h4": 40})


def daily_loss_limit_r(cfg):
    """Daily loss limit in R (0 = off). 1.5% of the account at 0.25% risk per trade = 6R."""
    if cfg.max_daily_loss_r is not None:
        return cfg.max_daily_loss_r
    return cfg.max_daily_loss_pct / cfg.risk_percent if cfg.risk_percent and cfg.max_daily_loss_pct else 0.0


PROFILES = ("strict", "active", "loose", "scalp", "swing", "golden", "forex")


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
    elif profile == "loose":
        # the most permissive profile: a score of 60+, a strong confirmation candle may also be one whose
        # body is at least 60% of its range. 60% of the 8 confluence checks is 5, which is what strict
        # already needs, so the loose profile keeps active's 4. Nothing mandatory is relaxed.
        base = dict(profile="loose", min_confluence=4, retest_window=5, max_zone_age=192,
                    fvg_max_fill=0.7, ready_score=60, strong_body_frac=0.6)
    elif profile == "scalp":
        # MODE 1 - SCALP. Trend: H1 + M15 structure must both agree. Zone of interest: M15 FVG/OB retested on M5.
        # Entry confirmation: the closed M5 candle plus the last closed M1 and M3 candles. Killzones UTC: London
        # 08:00-11:00, New York 13:00-16:30 (muted otherwise, so also in the Asian session). Strict sequence:
        # liquidity sweep -> MSS with displacement >= 1.5 ATR -> retest of a FRESH FVG/OB; never counter-trend.
        # Score 85+, net R:R >= 1.5 after the spread margin (target 1:2, TP1 at 1:1). Not measured on live results.
        base = dict(profile="scalp", min_confluence=4, retest_window=5, max_zone_age=192, fvg_max_fill=0.7,
                    ready_score=85, rr=2.0, min_net_rr=1.5, max_risk_atr=2.0, htf_mode="h1m15", m1_confirm=True,
                    strict_sequence=True, require_sweep=True, require_mss_fvg=True, min_displacement=1.5,
                    late_entry_frac=0.5, spread_margin=1.25, killzones=((8.0, 11.0), (13.0, 16.5)))
    elif profile == "golden":
        # active's entry rules, but only when price is in the Fibonacci golden zone (62-79% retracement
        # of the latest M15 leg, in the trade's direction). Target 1:2. Not measured yet.
        base = dict(profile="golden", min_confluence=4, retest_window=5, max_zone_age=192,
                    fvg_max_fill=0.7, ready_score=75, require_fib=True)
    elif profile == "forex":
        # FX pairs (EURUSD, GBPJPY, JPYGBP): H4/H1 bias, the closed M15 candle is the PRIMARY confirmation
        # (the signal direction comes from it), M5 only adds precision points and never triggers alone.
        # Score 0-100 with its own weights; 70+ moderate, 80+ strong, 90+ very strong. TP1 1:1.5, TP2 1:3.
        base = dict(profile="forex", forex=True, single_active=True, rr=3.0, min_net_rr=1.5, max_risk_atr=2.5,
                    ready_score=70, watch_score=60, min_confluence=5, retest_window=5, max_zone_age=192,
                    fvg_max_fill=0.7, max_signal_age=420, strong_body_frac=0.6)
    elif profile == "swing":
        # MODE 2 - SWING. Trend: Daily + H4 structure must both agree. Zone of interest: H1 FVG/OB (the sweep and the
        # MSS are read on H1). Entry confirmation: a closed M15 candle that the closed M5 candle agrees with. Runs all
        # day except the last 2 hours before the daily close (20:00-22:00 UTC, not for crypto). Strict sequence as in
        # scalp. Score 80+, net R:R >= 1:3 after the spread margin (target 1:3.5, TP1 at 1:1.75). Not measured yet.
        base = dict(profile="swing", rr=3.5, min_net_rr=3.0, max_risk_atr=2.0, max_zone_age=384, ready_score=80,
                    min_confluence=5, retest_window=8, htf_mode="d1h4", struct_tf="h1", confirm_tf="m15",
                    strict_sequence=True, require_sweep=True, require_mss_fvg=True, min_displacement=1.5,
                    late_entry_frac=0.5, spread_margin=1.25, blackout=((20.0, 22.0),), max_signal_age=420,
                    h4_zones=True)
    elif profile == "strict":
        base = {}
    else:
        raise ValueError("PROFILE must be strict, active, loose, scalp, swing, golden or forex")
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


def confirmation(m5, side, body_frac=None):
    """Checks the LAST candle of `m5` (must already be closed). Returns (ok, strong, reason).
    strong = it also closed beyond the previous candle's high (buy) / low (sell), or, when `body_frac`
    is given, its body is at least that share of its high-low range."""
    if len(m5) < 2:
        return False, False, "not enough M5 candles"
    c, p = m5[-1], m5[-2]
    if side == "buy":
        if not c["close"] > c["open"]:
            return False, False, "last closed M5 candle is not bullish"
        return True, c["close"] > p["high"] or _body_strong(c, body_frac), ""
    if not c["close"] < c["open"]:
        return False, False, "last closed M5 candle is not bearish"
    return True, c["close"] < p["low"] or _body_strong(c, body_frac), ""


def _body_strong(c, frac):
    rng = c["high"] - c["low"]
    return bool(frac) and rng > 0 and abs(c["close"] - c["open"]) >= frac * rng


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


def htf_alignment(h4, h1, m15, side, cfg, m5=None, m1=None, d1_trend=None):
    """(ok, label). H4 and H1 must agree with the trade and M15 must be in the same direction.
    A trade against H4 is allowed only after a recent H1 MSS in its direction (confirmed reversal).
    With cfg.ltf_bias (scalp profile) the direction comes from M5 and M1 structure instead (M1 counts
    when its candles are available; without them M5 alone decides); the only higher-timeframe rule kept
    is that H4 and H1 must not BOTH be against the trade."""
    want = "up" if side == "buy" else "down"
    e4, e1, e15 = (st.structure_events(h4, cfg.htf_k), st.structure_events(h1, cfg.htf_k),
                   st.structure_events(m15, 2))
    t4, t1, t15 = st.current_trend(e4), st.current_trend(e1), st.current_trend(e15)
    if cfg.htf_mode in ("h1m15", "d1h4"):
        if cfg.htf_mode == "h1m15":
            a, b, la, lb = t1, t15, "H1", "M15"
        else:
            a, b, la, lb = d1_trend, t4, "D1", "H4"
        if a != want or b != want:
            return False, f"{la} {a or 'unknown'} / {lb} {b or 'unknown'}: both must be {want} for a {side}"
        return True, f"{la} and {lb} {want}"
    if cfg.ltf_bias and m5 is not None:
        t5 = st.current_trend(st.structure_events(m5, 2))
        t1m = st.current_trend(st.structure_events(m1, 2)) if m1 else None
        if t5 != want or (t1m is not None and t1m != want):
            return False, (f"scalp bias: M5 {t5 or 'unknown'}, M1 {t1m or 'unavailable'}, "
                           f"need both {want} for a {side}")
        if t4 is not None and t4 == t1 and t4 != want:
            return False, f"scalp bias: H4 and H1 are both {t4}, against a {side}"
        return True, (f"scalp bias: M5 {want}" + (f" and M1 {want}" if t1m else " (M1 unavailable)")
                      + f" (M15 {t15 or '?'}, H4 {t4 or '?'}, H1 {t1 or '?'})")
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


def fib_check(m15, side, entry):
    """(inside, text): is `entry` in the 62-79% golden zone of the latest M15 leg in the trade's
    direction (an up leg for a buy, a down leg for a sell)? inside is None when there is no leg."""
    pd = st.premium_discount(m15, 3)
    want = "up" if side == "buy" else "down"
    if not pd:
        return None, "Fib: no clear M15 leg yet"
    if pd["direction"] != want:
        return False, f"Fib: the latest M15 leg is {pd['direction']}, a {side} needs a {want} leg"
    rng = pd["high"] - pd["low"]
    ret = (pd["high"] - entry) / rng if want == "up" else (entry - pd["low"]) / rng
    lo, hi = st.OTE
    inside = lo - 0.02 <= ret <= hi + 0.02
    return inside, (f"Fib: entry at {ret * 100:.0f}% of the M15 leg {pd['low']:.5g}-{pd['high']:.5g}, "
                    f"{'INSIDE' if inside else 'outside'} the {int(lo * 100)}-{int(hi * 100)}% golden zone")


def tf_agreement(side, m1, m5, m15, h1, h4, cfg):
    """Structure direction of every timeframe against the trade: '✓' agrees, '✗' against, '?' unknown.
    Information for the reader (M1 is timing only); it never blocks a signal by itself."""
    want = "up" if side == "buy" else "down"
    out = []
    for label, bars, k in (("M1", m1, 2), ("M5", m5, 2), ("M15", m15, 2), ("H1", h1, cfg.htf_k), ("H4", h4, cfg.htf_k)):
        try:
            tr = st.current_trend(st.structure_events(bars, k)) if bars else None
        except Exception:
            tr = None
        out.append(f"{label} {'?' if tr is None else '✓' if tr == want else '✗'}")
    return " ".join(out)


def is_crypto(market):
    return str(market).upper().startswith(("BTC", "ETH", "SOL", "XRP", "BNB"))


def h4_zone_fallback(rev, side, h1, h4, cfg):
    """Swing: when the H1 sweep + MSS exist but the MSS move made no H1 zone, the fresh valid H4 FVG / order
    block on the right side may be the zone of interest. Returns the extended sequence (complete) or rev."""
    if not rev.get("event") or rev.get("complete") or rev["stage"] != 3 or not rev.get("sweep"):
        return rev
    want = "bull" if side == "buy" else "bear"
    times = [int(c["time"].timestamp()) for c in h1]

    def to_h1(i):
        t = int(h4[i]["time"].timestamp())
        return next((k for k, tt in enumerate(times) if tt >= t), len(h1) - 1)

    zones = []
    for f in fvg_candidates(h4, cfg):
        if f["valid"] and f["side"] == want and f.get("status", "fresh") == "fresh":
            zones.append(dict(f, kind="FVG", idx=to_h1(f["idx"]), tf="H4"))
    for o in ob_candidates(h4, cfg):
        if o["valid"] and o["side"] == want and o.get("status", "fresh") == "fresh":
            zones.append(dict(o, kind="OB", idx=to_h1(o["idx"]), break_idx=to_h1(o["break_idx"]), tf="H4"))
    if not zones:
        return rev
    sweep, event = rev["sweep"], rev["event"]
    after = h1[event["idx"]:]
    if side == "buy":
        lo, hi = sweep["extreme"], max(c["high"] for c in after)
    else:
        lo, hi = min(c["low"] for c in after), sweep["extreme"]
    typ = "SSL" if side == "buy" else "BSL"
    out = dict(rev, zones=zones, stage=4, complete=True, leg={"low": lo, "high": hi, "eq": (lo + hi) / 2},
               event_id=f"{typ}-{sweep['level']}-{round(sweep['price'], 6)}-{int(sweep['time'].timestamp())}")
    out["reasons"] = []
    return out


def in_killzone(now_ts, zones):
    """True when the UTC hour of now_ts is inside one of the (start, end) windows; no windows = always."""
    if not zones:
        return True
    hour = (now_ts % 86400) / 3600.0
    return any(a <= hour < b for a, b in zones)


def m3_from_m1(m1):
    """Closed M3 candles built from closed M1 candles (only groups with all 3 minutes), oldest first."""
    groups = {}
    for c in m1 or []:
        groups.setdefault(int(c["time"].timestamp()) // 180, []).append(c)
    out = []
    for k in sorted(groups):
        g = sorted(groups[k], key=lambda c: c["time"])
        if len(g) == 3:
            out.append({"time": g[0]["time"], "open": g[0]["open"], "close": g[-1]["close"],
                        "high": max(c["high"] for c in g), "low": min(c["low"] for c in g)})
    return out


def m1_m3_agree(m1, side):
    """(ok, text): the last closed M1 and M3 candles both point the trade's way."""
    if not m1:
        return False, "M1 data unavailable: the scalp entry needs M1/M3 confirmation"
    m3 = m3_from_m1(m1)
    if not m3:
        return False, "not enough M1 candles to build an M3 candle"
    up = side == "buy"
    ok1 = (m1[-1]["close"] > m1[-1]["open"]) == up and m1[-1]["close"] != m1[-1]["open"]
    ok3 = (m3[-1]["close"] > m3[-1]["open"]) == up and m3[-1]["close"] != m3[-1]["open"]
    if ok1 and ok3:
        return True, "M1 and M3 closed candles confirm"
    return False, f"M1/M3 do not confirm yet (M1 {'ok' if ok1 else 'against'}, M3 {'ok' if ok3 else 'against'})"


def late_entry(bars_after, side, entry, tp1, frac):
    """True when, after the zone formed, price already travelled more than `frac` of the way from the entry
    to TP1 and came back: the move is spent and the signal is cancelled."""
    if not frac or not bars_after or tp1 == entry:
        return False
    if side == "buy":
        done = (max(c["high"] for c in bars_after) - entry) / (tp1 - entry)
    else:
        done = (entry - min(c["low"] for c in bars_after)) / (entry - tp1)
    return done > frac


def cost_check(risk, spread, commission_price, cfg):
    """(ok, cost, note). cost = spread + commission + slippage in price units."""
    if spread is None and not cfg.allow_unknown_cost:
        return False, None, "spread unknown (set SPREAD_<SYMBOL> or fill markets.json)"
    cost = (spread or 0.0) * cfg.spread_margin + (commission_price or 0.0) + cfg.slippage
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


FOREX_SCORE_MAX = {"htf_h4h1": 15, "liquidity_sweep": 15, "bos_choch": 15, "fvg": 10, "ob": 10,
                   "retest": 15, "m15_confirm": 10, "m5_confirm": 5, "sr_alignment": 5}      # = 100


def forex_score(htf_ok, mode, sweep_scope, mss, bos, zone_event, zones, retested, m15_strong, m5_ok, m5_strong,
                sr_hit):
    """The forex 0-100 score, each part from a fixed rule (never a win probability):
    htf_h4h1 15 aligned / 8 complete reversal; liquidity_sweep 15 external or session / 12 internal / 10 swing;
    bos_choch 15 MSS (CHoCH with displacement) / 12 BOS / 6 only the zone's own event; fvg 10 fresh / 7 other;
    ob 10 fresh / 7 other; retest 15 when a valid zone was retested; m15_confirm 10 strong / 6 closed candle;
    m5_confirm 5 strong / 3 agreeing (optional); sr_alignment 5."""
    fvg = next((z for z in zones if z["kind"] == "FVG"), None)
    ob = next((z for z in zones if z["kind"] == "OB"), None)
    pts = lambda z: 0 if z is None else 10 if z.get("status", "fresh") == "fresh" else 7   # noqa: E731
    parts = {"htf_h4h1": 15 if htf_ok else 8 if mode == "reversal" else 0,
             "liquidity_sweep": {"external": 15, "session": 15, "internal": 12, "swing": 10}.get(sweep_scope, 0),
             "bos_choch": 15 if mss else 12 if bos else 6 if zone_event else 0,
             "fvg": pts(fvg), "ob": pts(ob), "retest": 15 if retested else 0,
             "m15_confirm": 10 if m15_strong else 6, "m5_confirm": 5 if m5_strong else 3 if m5_ok else 0,
             "sr_alignment": 5 if sr_hit else 0}
    return parts, sum(parts.values())


def markets_pip(market):
    import markets
    return markets.pip_size(market)


def forex_strength(score):
    """70-79 moderate, 80-89 strong, 90-100 very strong; below 70 no signal."""
    return "VERY STRONG" if score >= 90 else "STRONG" if score >= 80 else "MODERATE" if score >= 70 else "NONE"


def low_liquidity(now_ts):
    """UTC weekend, or the 21:00-24:00 UTC daily rollover when spreads widen and liquidity is thin."""
    import time as _t
    g = _t.gmtime(now_ts)
    if g.tm_wday >= 5 and not (g.tm_wday == 6 and g.tm_hour >= 21):
        return "weekend (market closed)"
    if g.tm_wday == 4 and g.tm_hour >= 21:
        return "Friday close"
    if 21 <= g.tm_hour < 24:
        return "daily rollover (low liquidity, wide spreads)"
    return ""


def abnormal_volatility(m15, lookback=96, ratio=2.5):
    """'' when the M15 ATR is normal, else a text: the current ATR is `ratio` times its recent median."""
    if len(m15) < 40:
        return ""
    now = st.atr_at(m15, len(m15))
    hist = sorted(st.atr_at(m15, i) for i in range(max(20, len(m15) - lookback), len(m15)))
    med = hist[len(hist) // 2] if hist else 0
    if med > 0 and now > ratio * med:
        return f"abnormal volatility (M15 ATR {now:.5g} is {now / med:.1f}x its median)"
    return ""


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
    m5, c15, h1, h4 = bars["m5"], bars["m15"], bars["h1"], bars["h4"]
    m15 = bars[cfg.struct_tf]                        # structure / zones timeframe (M15; H1 for swing)
    last = m5[-1]
    m15_conf = cfg.forex or cfg.confirm_tf == "m15"  # the trigger candle is a closed M15 (forex, swing)
    primary = c15[-1] if m15_conf else last          # the candle that must have just closed
    close_time = int(primary["time"].timestamp()) + TF_SECONDS["m15" if m15_conf else "m5"]
    if now_ts - close_time > cfg.max_signal_age:
        res["reasons"].append(f"the confirmation candle is too old" + (" (M15)" if cfg.forex else ""))
        return res
    if cfg.forex:
        quiet = low_liquidity(now_ts) or abnormal_volatility(m15)
        if quiet:
            res["reasons"].append(f"no signal: {quiet}")
            return res
    if not in_killzone(now_ts, cfg.killzones):
        res["reasons"].append("outside the London / New York killzones (UTC): no new signal")
        return res
    if cfg.blackout and not is_crypto(market) and in_killzone(now_ts, cfg.blackout):
        res["reasons"].append("last hours before the daily market close (UTC): no new swing signal")
        return res
    side = "buy" if primary["close"] > primary["open"] else "sell" if primary["close"] < primary["open"] else None
    if side is None:
        res["reasons"].append(f"last closed {'M15' if cfg.forex else 'M5'} candle is a doji")
        return res
    rep["confirm"] = (f"closed {'M15' if cfg.forex else 'M5'} {_ohlc(primary)} "
                      f"({'bullish' if side == 'buy' else 'bearish'})")
    rep["m1"] = m1_timing(m1, side)

    ok_c, strong, why = confirmation(c15 if m15_conf else m5, side, cfg.strong_body_frac)
    checks = res["checks"]
    checks["confirm"] = ok_c
    if not ok_c:
        res["reasons"].append(why.replace("M5", "M15") if cfg.forex else why)
        return res
    if strong:
        rep["confirm"] += ", strong (closed beyond the previous candle or a large body)"
    m5_ok, m5_strong, _ = confirmation(m5, side, cfg.strong_body_frac) if m15_conf else (False, False, "")
    if cfg.require_m15_confirm and not m15_conf:
        ok15, _, why15 = confirmation(c15, side, cfg.strong_body_frac)
        if not ok15:
            res["reasons"].append(f"M15 candle does not confirm yet: {why15}")
            return res
    if m15_conf and not cfg.forex and not m5_ok:
        res["reasons"].append("M5 does not confirm the M15 candle yet")
        return res
    if cfg.m1_confirm:
        ok_m1, why_m1 = m1_m3_agree(m1, side)
        if not ok_m1:
            res["reasons"].append(why_m1)
            return res
    if cfg.forex:
        rep["m5_precision"] = (f"M5 {'agrees' if m5_ok else 'does not agree yet'}"
                               f"{' (strong)' if m5_strong else ''}: precision only, never a trigger")

    want = "bull" if side == "buy" else "bear"
    role = "support" if side == "buy" else "resistance"
    fv_all = fvg_candidates(m15, cfg)
    ob_all = ob_candidates(m15, cfg)
    rev = ict_engine.reversal_sequence(m15, side, fv_all, ob_all, calendar)
    if cfg.h4_zones and cfg.struct_tf == "h1":
        rev = h4_zone_fallback(rev, side, h1, h4, cfg)
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
    d1_trend, d1_label = ict_engine.d1_context(d1)
    ok_h, label = htf_alignment(h4, h1, c15, side, cfg, m5, m1, d1_trend)
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
        if d1_trend and not cfg.ltf_bias and cfg.htf_mode == "default" and d1_trend != ("up" if side == "buy" else "down"):
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

    if cfg.strict_sequence:
        if not checks["htf"]:
            res["reasons"].append(f"against the {cfg.htf_mode.upper()} trend (no counter-trend in this mode): {label}")
            return res
        if not seq_ok:
            res["reasons"].append("no complete sequence: liquidity sweep -> MSS with displacement -> retest of a fresh FVG/OB")
            return res
    if cfg.require_sweep and not checks["liquidity"]:
        res["reasons"].append("no valid liquidity sweep: this profile only enters after a sweep")
        return res

    fib_ok, fib_text = fib_check(m15, side, last["close"])
    if cfg.require_fib and not fib_ok:
        res["reasons"].append("not in the Fibonacci golden zone: " + fib_text.replace("Fib: ", ""))
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
    tp1 = entry + 0.5 * cfg.rr * risk if side == "buy" else entry - 0.5 * cfg.rr * risk
    zi = primary.get("idx")
    if cfg.late_entry_frac and zi is not None and 0 <= zi < len(m15):
        t0 = int(m15[zi]["time"].timestamp()) + 3 * TF_SECONDS[cfg.struct_tf]
        touched = [i for i, c in enumerate(m5) if int(c["time"].timestamp()) >= t0
                   and c["low"] <= primary["high"] and c["high"] >= primary["low"]]
        if touched and touched[0] < len(m5) - 1 and late_entry(m5[touched[0]:], side, entry, tp1, cfg.late_entry_frac):
            res["reasons"].append(f"late entry: price already travelled over {cfg.late_entry_frac:.0%} of the way "
                                  "to TP1 before coming back to the zone, signal cancelled")
            return res
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
    if cfg.strict_sequence:
        ei = rev["event"]["idx"]
        a_ev = st.atr_at(m15, ei)
        r_ev = abs(m15[ei]["close"] - m15[ei]["open"]) / a_ev if a_ev > 0 else 0.0
        if r_ev < cfg.min_displacement:
            res["reasons"].append(f"MSS displacement {r_ev:.2f} ATR is under {cfg.min_displacement:g} ATR")
            return res
        if not any(z["kind"] in ("FVG", "OB") and z.get("status", "fresh") == "fresh" for z in fo_hit):
            res["reasons"].append("the retested FVG/OB is not fresh (already used or partly filled)")
            return res
    if cfg.require_mss_fvg:
        mss_ok = bool(rev["event"] and seq_ok) or any(e["mss"] and e["displacement"] for e in recent_ev)
        fvg_ok = any(z["kind"] == "FVG" for z in fo_hit) or any(
            f["side"] == want and f["valid"] and len(m15) - 1 - f["idx"] <= 20 for f in fv_all)
        if not mss_ok:
            res["reasons"].append("no internal MSS/CHoCH with displacement: a zone touch alone is not enough")
            return res
        if max(ratios, default=0.0) < cfg.min_displacement:
            res["reasons"].append(f"displacement too weak ({max(ratios, default=0.0):.2f} ATR, needs {cfg.min_displacement:g})")
            return res
        if not fvg_ok:
            res["reasons"].append("no valid fair value gap (FVG) after the structure break")
            return res
    vd = None
    if cfg.volume_filter and (cfg.strict_sequence or cfg.require_mss_fvg):
        disp_idx = (rev["event"]["idx"] if rev["event"] and seq_ok else
                    max([e["idx"] for e in recent_ev if e["mss"] and e["displacement"]], default=None))
        if disp_idx is None:
            res["reasons"].append("no displacement candle to confirm with volume")
            return res
        formed = any(f["side"] == want and f["valid"] and abs(f["idx"] - 1 - disp_idx) <= 1 for f in fv_all)
        vd = indicators.volume_displacement(m15, disp_idx, True, formed, cfg.vol_mult, cfg.body_mult,
                                            indicators.VOLUME_LOOKBACK, cfg.volume_required)
        res["volume"] = vd
        rep["volume"] = vd["text"]
        if not vd["valid"]:
            res["reasons"].append("displacement not confirmed: " + "; ".join(vd["reasons"]))
            return res
    net_rr = (cfg.rr * risk - cost) / (risk + cost)
    if cfg.forex:
        parts, score100 = forex_score(
            checks["htf"], mode, scope,
            mss=bool(rev["event"] and seq_ok) or any(e["mss"] for e in recent_ev),
            bos=any(e["type"] == "BOS" for e in recent_ev), zone_event=any(z.get("event") for z in fo_hit),
            zones=fo_hit, retested=bool(tested), m15_strong=strong, m5_ok=m5_ok, m5_strong=m5_strong,
            sr_hit=bool(sr_hit))
    else:
        parts, score100 = score_breakdown(
            checks["htf"], mode, scope,
            mss=bool(rev["event"] and seq_ok) or any(e["mss"] for e in recent_ev),
            bos=any(e["type"] == "BOS" for e in recent_ev), zone_event=any(z.get("event") for z in fo_hit),
            disp_ratio=max(ratios, default=0.0), zone_status=top.get("status", "fresh") if top else None,
            sr_hit=bool(sr_hit), confirm=ok_c, strong=strong, cost_ok=ok_cost, net_rr=net_rr,
            cost_unverified=bool(note))
    vw = indicators.vwap_view(m5, market, now_ts, entry, side)
    ax = indicators.adx_view(c15, side)
    mom = indicators.momentum_view(c15, side)         # display only, not part of any gate or score
    ctx = indicators.context_points(vw, ax)           # at most +6, only context (VWAP side, ADX trend strength)
    score100 = min(100, score100 + ctx)
    band = score_band(score100, cfg)
    res.update(score100=score100, score_parts=parts, band=band, score_context=ctx, vwap=vw, adx=ax)
    if cfg.forex:
        res["strength"] = forex_strength(score100)
    if band == "READY" and cfg.counter_trend_min_score and mode == "reversal" and not ok_h:
        if score100 < cfg.counter_trend_min_score or not ok_c:
            band = "WATCH"
            res["band"] = band
            res["reasons"].insert(0, f"score {score100}/100 is WATCH: COUNTER-TREND needs {cfg.counter_trend_min_score}+ "
                                  f"and a closed confirmation candle (confirmed: {'yes' if ok_c else 'no'}). Parts: "
                                  + ", ".join(f"{k} {v}/{SCORE_MAX.get(k) or FOREX_SCORE_MAX[k]}" for k, v in parts.items()))
            return res
    if band != "READY":
        res["reasons"].append(f"score {score100}/100 is {band}: a signal needs {cfg.ready_score}+ "
                              f"(WATCH {cfg.watch_score}-{cfg.ready_score - 1}). Parts: "
                              + ", ".join(f"{k} {v}/{SCORE_MAX.get(k) or FOREX_SCORE_MAX[k]}" for k, v in parts.items()))
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
                    "counter_trend": mode == "reversal" and not ok_h, "fib": fib_text,
                    "tp1": tp1, "tp3": entry + 1.5 * cfg.rr * risk if side == "buy" else entry - 1.5 * cfg.rr * risk,
                    "tp4": entry + 2.0 * cfg.rr * risk if side == "buy" else entry - 2.0 * cfg.rr * risk,
                    "tp5": entry + 2.5 * cfg.rr * risk if side == "buy" else entry - 2.5 * cfg.rr * risk,
                    "vwap": vw, "adx": ax, "momentum": mom, "score_context": ctx, "volume": vd,
                    "trends": {"h4": st.current_trend(st.structure_events(h4, cfg.htf_k)) or "unknown",
                               "h1": st.current_trend(st.structure_events(h1, cfg.htf_k)) or "unknown",
                               "m15": st.current_trend(st.structure_events(c15, 2)) or "unknown"},
                    "strength": forex_strength(score100) if cfg.forex else None,
                    "pip": markets_pip(market), "risk": risk, "atr15": atr15,
                    "tf": tf_agreement(side, m1, m5, m15, h1, h4, cfg),
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


def risk_line(s):
    """Pip distance and ATR assessment of the stop (forex signals); '' when there is no pip size."""
    if not s.get("pip") or not s.get("risk"):
        return ""
    pips = s["risk"] / s["pip"]
    atr = (f", stop = {s['risk'] / s['atr15']:.1f} M15 ATR ({s['atr15'] / s['pip']:.0f} pips)"
           if s.get("atr15") else "")
    return f"Stop distance: {pips:.1f} pips{atr}"


def entry_checklist(s, res, ar=False):
    """Pre-entry checklist lines: what the bot verified (from this signal) and what only you can check."""
    c = res["checks"]
    unverified = bool(s.get("cost_note"))
    net = (abs(s["tp"] - s["entry"]) - s["cost"]) / (abs(s["entry"] - s["sl"]) + s["cost"]) if s["entry"] != s["sl"] else 0
    auto = [("closed-candle confirmation", c.get("confirm")), ("retest of a valid zone", c.get("retest")),
            ("HTF direction" if not s.get("counter_trend") else "COUNTER-TREND (strict gate passed)", c.get("htf") or s.get("counter_trend")),
            (f"net R:R {net:.2f} after the spread margin", c.get("cost") and net >= 1.5),
            ("spread verified" if not unverified else "spread NOT verified", not unverified)]
    manual = ["spread now in MT5 is not wider than assumed", "price has not run away from the entry (do not chase)",
              "no high-impact news in the next 30 minutes", "stop and lot size set before pressing the button",
              "no other open trade on this market that you do not want doubled"]
    if ar:
        tr = {"closed-candle confirmation": "تأكيد بشمعة مغلقة", "retest of a valid zone": "إعادة اختبار منطقة صالحة",
              "HTF direction": "اتجاه الفريمات الكبيرة", "COUNTER-TREND (strict gate passed)": "عكس الاتجاه (اجتاز الشرط المشدد)",
              "spread verified": "السبريد موثّق", "spread NOT verified": "السبريد غير موثّق"}
        mt = ["السبريد الحالي في MT5 ليس أوسع من المفترض", "السعر لم يبتعد عن الدخول (لا تطارد السعر)",
              "لا خبر عالي التأثير خلال 30 دقيقة", "حدّد الستوب واللوت قبل الضغط على الزر",
              "لا توجد صفقة مفتوحة أخرى على نفس السوق لا تريد مضاعفتها"]
        a = [f"{'✔' if ok else '✘'} " + (f"صافي R:R {net:.2f} بعد هامش السبريد" if k.startswith("net R:R") else tr.get(k, k)) for k, ok in auto]
        return ["قائمة التحقق قبل الدخول:", "  تحقق البوت: " + " | ".join(a),
                "  تحقق أنت: " + " | ".join("☐ " + m for m in mt)]
    return ["Pre-entry checklist:", "  Checked by the bot: " + " | ".join(f"{'OK' if ok else 'NO'} {k}" for k, ok in auto),
            "  Check yourself: " + " | ".join("[ ] " + m for m in manual)]


def format_signal(market, res, digits=2):
    s = res["setup"]
    c = res["checks"]
    mark = lambda k: "yes" if c.get(k) else "no"  # noqa: E731
    tag = {"active": ", ACTIVE profile (looser filter)",
           "loose": ", LOOSE profile (lowest filter, score 60+)",
           "scalp": ", SCALP (quick near target)",
           "swing": ", SWING (far target, hold longer)",
           "golden": ", GOLDEN ZONE (Fibonacci 62-79%)",
           "forex": ", FOREX"}.get(s.get("profile"), "")
    rr = abs(s["tp"] - s["entry"]) / abs(s["entry"] - s["sl"]) if s["entry"] != s["sl"] else 2.0
    if s.get("profile") not in ("scalp", "forex") and s.get("score100", 100) < 86:
        tag += ", REDUCED CONFIDENCE (score below 86)"
    lines = [f"SELECTIVE SIGNAL: {s['side'].upper()} {market}  [{s['mode']}, data {res['status']}{tag}{', COUNTER-TREND' if s.get('counter_trend') else ''}]",
             f"Entry: {s['entry']:.{digits}f}  Invalidation: {s['invalidation']:.{digits}f}  "
             + (f"SL: {s['sl']:.{digits}f}  TP1: {s['tp1']:.{digits}f} (1:{rr / 2:g}, take partial and move SL to entry)  "
                f"TP2: {s['tp']:.{digits}f} (1:{rr:g})" if s.get("tp1") else
                f"SL: {s['sl']:.{digits}f}  TP: {s['tp']:.{digits}f}  (R:R 1:{rr:g} before costs)"),
             risk_line(s),
             f"Score {res['score100']}/100 ({res.get('strength') or res['band']}; a quality ranking, NOT a win probability) | "
             + ", ".join(f"{k} {v}/{SCORE_MAX.get(k) or FOREX_SCORE_MAX[k]}" for k, v in res["score_parts"].items()),
             f"Confluence {res['score']}/8: HTF {mark('htf')}, S/R {mark('sr')}, liquidity {mark('liquidity')}, "
             f"BOS/MSS {mark('bos_mss')}, FVG/OB {mark('fvg_ob')}, retest {mark('retest')}, "
             f"M5 close {mark('confirm')}{' (strong)' if s['strong_confirmation'] else ''}, cost {mark('cost')}",
             "Zones: " + "; ".join(s["zones"]),
             f"Timeframes (structure vs the trade): {s['tf']}",
             s.get("fib", ""),
             f"Cost assumed: {s['cost']:.4g} price units" + (f" ({s['cost_note']})" if s["cost_note"] else ""),
             *entry_checklist(s, res),
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
    rr = abs(s["tp"] - s["entry"]) / abs(s["entry"] - s["sl"]) if s["entry"] != s["sl"] else 2.0
    yn = lambda k: "نعم" if c.get(k) else "لا"  # noqa: E731
    side = "شراء" if s["side"] == "buy" else "بيع"
    mode = "انعكاس بعد سحب سيولة" if s["mode"] == "reversal" else "استمرار الاتجاه"
    name = AR_NAMES.get(market, market)
    cost = f"{s['cost']:.4g}"
    if s["cost_note"]:
        cost += " (غير موثّقة: السبريد غير معروف، تحقق منه في MT5)"
    lines = [f"إشارة دخول: {side} {name} ({market})",
             f"النوع: {mode} | حالة البيانات: {res['status']}"
             + {"active": " | ملف نشط (فلتر أخف)", "loose": " | ملف متساهل (أقل فلتر، تقييم 60+)",
                "scalp": " | سكالبينج (هدف قريب وسريع)",
                "swing": " | سوينغ (هدف بعيد، احتفظ بالصفقة أطول)",
                "golden": " | المنطقة الذهبية (فيبوناتشي 62-79%)",
                "forex": " | فوركس"}.get(s.get("profile"), "")
             + (" | ثقة أقل (التقييم أقل من 86)" if s.get("profile") not in ("scalp", "forex") and s.get("score100", 100) < 86 else "")
             + (" | عكس الاتجاه" if s.get("counter_trend") else ""),
             f"الدخول: {s['entry']:.{digits}f}",
             f"الإبطال: {s['invalidation']:.{digits}f}",
             f"وقف الخسارة: {s['sl']:.{digits}f}",
             (f"الهدف الأول TP1: {s['tp1']:.{digits}f} (1:{rr / 2:g}، خذ جزءًا وانقل الستوب للدخول) | "
              f"الهدف الثاني TP2: {s['tp']:.{digits}f} (1:{rr:g})" if s.get("tp1") else
              f"الهدف: {s['tp']:.{digits}f}  (نسبة 1:{rr:g} قبل التكاليف)"),
             (f"المسافة للستوب: {s['risk'] / s['pip']:.1f} نقطة" if s.get("pip") and s.get("risk") else ""),
             f"التقييم {res['score100']}/100 (ترتيب لجودة الإعداد وليس احتمال ربح)",
             f"التوافق {res['score']}/8: الاتجاه {yn('htf')}، دعم/مقاومة {yn('sr')}، سيولة {yn('liquidity')}، "
             f"BOS/MSS {yn('bos_mss')}، FVG/OB {yn('fvg_ob')}، إعادة اختبار {yn('retest')}، "
             f"شمعة M5 مغلقة {yn('confirm')}{' (قوية)' if s['strong_confirmation'] else ''}، التكلفة {yn('cost')}",
             f"المناطق: {_ar('; '.join(s['zones']))}",
             f"الفريمات (البنية مقابل الصفقة): {s['tf']}",
             (s.get("fib", "").replace("Fib:", "فيبوناتشي:").replace("INSIDE", "داخل").replace("outside", "خارج")
              .replace("golden zone", "المنطقة الذهبية")),
             f"اتجاه الفريمات الكبيرة: {_ar(r['htf'])} | {_ar(r['d1'])}",
             f"السيولة: {_ar(r['liquidity'])}",
             f"الهيكل: {_ar(r['structure'])}",
             f"شمعة التأكيد M5: {_ar(r['confirm'])}",
             f"التكلفة المفترضة: {cost}",
             *entry_checklist(s, res, ar=True),
             "هذه إشارة فقط: نفّذ الصفقة بنفسك في MT5 بعد مقارنة السعر. لا توجد نسبة نجاح مضمونة."]
    return "\n".join(lines)


_ZONE_WORDS = {"FVG": ("فجوة القيمة العادلة FVG", "fair value gap (FVG)"), "OB": ("بلوك الأوامر OB", "order block (OB)")}


def zone_watch(market, data, now_ts, cfg, calendar="utc", lang="en"):
    """Price has just reached a decisive zone (it sits in a FVG / OB made by a complete sweep + MSS) but the entry candle has
    not closed yet. Returns a list of {'id', 'side', 'state', 'text'} with a plain explanation. Never a signal."""
    out = []
    try:
        bars = {tf: closed_only(data.get(tf) or [], sec, now_ts) for tf, sec in TF_SECONDS.items()}
        if any(len(bars[tf]) < cfg.min_bars[tf] for tf in bars):
            return out
        m5, c15, h1, h4 = bars["m5"], bars["m15"], bars["h1"], bars["h4"]
        struct = bars[cfg.struct_tf]
        fv_all, ob_all = fvg_candidates(struct, cfg), ob_candidates(struct, cfg)
        recent = m5[-cfg.retest_window:]
        for side in ("buy", "sell"):
            rev = ict_engine.reversal_sequence(struct, side, fv_all, ob_all, calendar)
            if not rev["complete"]:
                continue
            for z in rev["zones"]:
                if not any(c["low"] <= z["high"] and c["high"] >= z["low"] for c in recent):
                    continue
                if (side == "buy" and m5[-1]["close"] < z["low"] - (z["high"] - z["low"])) or \
                        (side == "sell" and m5[-1]["close"] > z["high"] + (z["high"] - z["low"])):
                    continue                                    # price already ran through the zone
                ok_h, htf_label = htf_alignment(h4, h1, c15, side, cfg)
                sw, ev = rev["sweep"], rev["event"]
                ar_word, en_word = _ZONE_WORDS.get(z["kind"], (z["kind"], z["kind"]))
                up = side == "buy"
                if lang == "ar":
                    text = (f"📍 {market}: السعر وصل منطقة حاسمة لـ{'شراء' if up else 'بيع'} "
                            f"({ar_word} بين {z['low']:.5g} و{z['high']:.5g})\n"
                            f"ماذا حدث بالترتيب: 1) سحب السيولة: السوق كنس أوامر {'البيع' if up else 'الشراء'} عند {sw['level']} "
                            f"(أبعد نقطة {sw['extreme']:.5g}). 2) كسر الهيكل MSS بشمعة قوية: هذا يعني أن الاتجاه بدأ يتغير. "
                            f"3) ترك السعر {ar_word} في الحركة، وهو الآن يعود إليها.\n"
                            f"الاتجاه الكبير: {'متوافق' if ok_h else 'عكس الاتجاه الكبير (انعكاس)'}.\n"
                            f"ما ننتظره: شمعة {'M15' if cfg.forex else 'M5'} مغلقة في اتجاه {'الشراء' if up else 'البيع'} تثبت أن "
                            f"{'المشترين' if up else 'البائعين'} دافعوا عن المنطقة. بدونها لا دخول.\n"
                            f"يُلغى الإعداد إذا أُغلق السعر {'تحت' if up else 'فوق'} {sw['extreme']:.5g}.\n"
                            f"الحالة: WAITING FOR CANDLE CONFIRMATION، لا تدخل الآن. ({cfg.profile})")
                else:
                    text = (f"📍 {market}: price reached a decisive {'BUY' if up else 'SELL'} zone "
                            f"({en_word} {z['low']:.5g}-{z['high']:.5g})\n"
                            f"What happened, in order: 1) liquidity sweep: the market cleared {'sell' if up else 'buy'} stops at "
                            f"{sw['level']} (extreme {sw['extreme']:.5g}). 2) market structure shift (MSS) with a strong candle: "
                            f"the direction is starting to change. 3) that move left a {en_word}, and price is now back in it.\n"
                            f"Higher timeframes: {'aligned' if ok_h else 'against (a reversal)'}.\n"
                            f"What we wait for: a closed {'M15' if cfg.forex else 'M5'} {'bullish' if up else 'bearish'} candle showing "
                            f"{'buyers' if up else 'sellers'} defend the zone. Without it there is no entry.\n"
                            f"The setup is cancelled on a close {'below' if up else 'above'} {sw['extreme']:.5g}.\n"
                            f"State: WAITING FOR CANDLE CONFIRMATION, do not enter yet. ({cfg.profile})")
                out.append({"id": f"{market}-{side}-{_zone_id(dict(z, side=side))}", "side": side,
                            "state": "WAITING FOR CANDLE CONFIRMATION", "text": text})
    except Exception:            # an explanation must never stop the scan
        return []
    return out


def watch_observations(market, data, now_ts, cfg, calendar="utc", lang="en", approach_atr=0.5):
    """What the setup tracker needs: one observation per live setup (a liquidity sweep followed by an MSS / CHoCH with
    displacement), BUY and SELL judged independently. Each has the sweep extreme, whether stage 3 is confirmed and,
    once a fresh FVG / OB exists, where price is relative to it: 'in', 'near' (within approach_atr x ATR) or 'far'.
    Nothing here is a signal. Returns (observations, last closed M5 close or None)."""
    out, last_close = [], None
    try:
        bars = {tf: closed_only(data.get(tf) or [], sec, now_ts) for tf, sec in TF_SECONDS.items()}
        if any(len(bars[tf]) < cfg.min_bars[tf] for tf in bars):
            return out, None
        m5, struct = bars["m5"], bars[cfg.struct_tf]
        last_close = m5[-1]["close"]
        fv_all, ob_all = fvg_candidates(struct, cfg), ob_candidates(struct, cfg)
        atr = st.atr_at(struct, len(struct) - 1) or 0.0
        explain = {w["side"]: w["text"] for w in zone_watch(market, data, now_ts, cfg, calendar, lang)}
        recent = m5[-cfg.retest_window:]
        for side in ("buy", "sell"):
            rev = ict_engine.reversal_sequence(struct, side, fv_all, ob_all, calendar)
            sweep, event = rev.get("sweep"), rev.get("event")
            if not sweep:
                continue
            obs = {"sid": f"{market}-{side}-{sweep['type']}-{sweep['level']}-{int(sweep['time'].timestamp())}",
                   "market": market, "side": side, "level": sweep["level"], "extreme": sweep["extreme"],
                   "stage3": bool(event), "zone": None, "rel": None,
                   "developing": any("CHoCH without displacement" in r for r in rev.get("reasons", []))}
            zones = rev.get("zones") or []
            if rev.get("complete") and zones:
                z = min(zones, key=lambda q: 0 if q["low"] <= last_close <= q["high"] else min(abs(last_close - q["low"]), abs(last_close - q["high"])))
                dist = 0.0 if z["low"] <= last_close <= z["high"] else min(abs(last_close - z["low"]), abs(last_close - z["high"]))
                touching = any(c["low"] <= z["high"] and c["high"] >= z["low"] for c in recent)
                obs["zone"] = {"kind": z["kind"], "low": z["low"], "high": z["high"]}
                obs["rel"] = "in" if touching else "near" if atr and dist <= approach_atr * atr else "far"
                if obs["rel"] == "in" and side in explain:
                    obs["explain"] = explain[side]
            out.append(obs)
    except Exception:                       # the tracker must never stop the scan
        return [], last_close
    return out, last_close


_TRIGGER = {"swing": "M15 entry candle (H1 structure), M5 agrees", "forex": "M15 (primary), M5 execution check",
            "scalp": "M15 zone, M5 entry candle, M1/M3 timing"}


def _htf_note(s):
    """One plain sentence on how H4 / H1 relate to the direction (a rating factor, never silent about a conflict)."""
    tr = s.get("trends") or {}
    want = "up" if s["side"] == "buy" else "down"
    h4, h1 = tr.get("h4", "unknown"), tr.get("h1", "unknown")
    agree = [n for n, t in (("H4", h4), ("H1", h1)) if t == want]
    against = [f"{n} {t}" for n, t in (("H4", h4), ("H1", h1)) if t in ("up", "down") and t != want]
    if len(agree) == 2:
        return f"H4 and H1 both agree with the {s['side'].upper()}"
    if not against:
        return f"{' and '.join(agree) or 'no higher timeframe'} agree(s), the other is unknown: weaker support"
    why = "a counter-trend reversal (sweep + MSS with displacement + retest)" if s.get("counter_trend") else "see the structure lines above"
    return f"{', '.join(against)} is against the {s['side'].upper()}; accepted because: {why}"


def format_kenan(market, res, digits=2, news="not checked", session="not checked", ttl_hours=6.0):
    """The KENAN TRADER AI alert layout. Only called for a signal that passed every mandatory condition."""
    s, r, c = res["setup"], res["report"], res["checks"]
    f = lambda v: f"{v:.{digits}f}" if v is not None else "n/a"      # noqa: E731
    risk = abs(s["entry"] - s["sl"]) or 1.0
    rr = lambda p: f"1:{abs(p - s['entry']) / risk:.2f}"             # noqa: E731
    net = (abs(s["tp"] - s["entry"]) - s["cost"]) / (risk + s["cost"])
    tr = s.get("trends") or {}
    zones = [z for z in s.get("zones", [])]
    ob = "; ".join(z for z in zones if "OB" in z.upper()) or "none in this setup"
    fvg = "; ".join(z for z in zones if "FVG" in z.upper()) or "none in this setup"
    status = "ENTRY CONFIRMED" + (" (COUNTER-TREND REVERSAL: complete sweep + CHoCH + displacement + FVG + retest)"
                                  if s.get("counter_trend") else "")
    ts = datetime.fromtimestamp(s["bar_time"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    vw, ax = s.get("vwap") or {}, s.get("adx") or {}
    lines = ["KENAN TRADER AI", "",
             f"MARKET: {market}", f"DIRECTION: {s['side'].upper()}", f"SIGNAL STATUS: {status}", "",
             f"ENTRY: {f(s['entry'])}", f"STOP LOSS: {f(s['sl'])}",
             f"TP1: {f(s.get('tp1'))} ({rr(s['tp1']) if s.get('tp1') else 'n/a'}, take part, move the stop to entry)",
             f"TP2: {f(s['tp'])} ({rr(s['tp'])})",
             f"TP3: {f(s.get('tp3'))} ({rr(s['tp3']) if s.get('tp3') else 'n/a'}, runner)",
             f"TP4: {f(s.get('tp4'))} ({rr(s['tp4']) if s.get('tp4') else 'n/a'}, extended runner, display only)",
             f"TP5: {f(s.get('tp5'))} ({rr(s['tp5']) if s.get('tp5') else 'n/a'}, extended runner, display only)", "",
             f"TIMEFRAME: {_TRIGGER.get(s.get('profile'), 'M15 zone, M5 entry candle')} | profile {s.get('profile', '?').upper()}",
             f"H4 TREND: {tr.get('h4', 'n/a')}", f"H1 TREND: {tr.get('h1', 'n/a')}",
             f"M15 STRUCTURE: {tr.get('m15', 'n/a')}; {r['structure']}", "",
             f"LIQUIDITY SWEEP: {r['liquidity']}", f"BOS / CHoCH: {r['structure']}",
             f"ORDER BLOCK: {ob}", f"FVG: {fvg}",
             f"VWAP: {vw.get('text', 'n/a')}", f"ADX: {ax.get('text', 'n/a')}",
             f"RSI: {(s.get('momentum') or {}).get('rsi_text', 'n/a')}", f"MACD: {(s.get('momentum') or {}).get('macd_text', 'n/a')}",
             f"DISPLACEMENT VOLUME: {(s.get('volume') or {}).get('text', 'filter off')}",
             f"NEWS FILTER: {news}", f"SESSION: {session}",
             f"ANALYSIS STAGES: {s.get('stage_trail') or 'FINAL ' + s['side'].upper()}",
             f"HTF NOTE: {_htf_note(s)}", "",
             f"SIGNAL QUALITY: {res['score100']}/100 (base {res['score100'] - s.get('score_context', 0)}"
             f" + VWAP/ADX context {s.get('score_context', 0)}; a ranking of setup quality, not a win probability)",
             f"RISK / REWARD: TP2 {rr(s['tp'])} gross, net 1:{net:.2f} after cost {s['cost']:.4g}"
             + (" (COST UNVERIFIED: spread unknown, check MT5)" if s.get("cost_note") else ""), "",
             f"SIGNAL ID: {res.get('id') or s.get('uses', ['n/a'])[0]}", f"TIMESTAMP: {ts}",
             "SIGNAL EXPIRY: " + datetime.fromtimestamp(s["bar_time"] + int(ttl_hours * 3600), tz=timezone.utc).strftime(
                 "%Y-%m-%d %H:%M UTC") + f" ({ttl_hours:g} h after the signal candle, or earlier if invalidated)", "",
             f"ENTRY REASON: {s['mode']} setup, HTF {s['htf']}; zone(s) {', '.join(zones) or 'n/a'}; "
             f"confirmation {'strong ' if s.get('strong_confirmation') else ''}closed candle",
             f"INVALIDATION CONDITION: a close beyond {f(s['invalidation'])} cancels the setup",
             "Signal only: place the trade yourself in MT5. No win rate is guaranteed."]
    return "\n".join(lines)


def process(market, data, now_ts, store, cfg=None, spread=None, commission_price=None,
            status="LIVE", calendar="utc"):
    """evaluate() plus the one-active-signal-per-market lock. The lock is only taken when a
    signal is really issued."""
    res = evaluate(market, data, now_ts, cfg, spread, commission_price, status, calendar)
    cfg = cfg or SelectiveConfig()
    if res["action"] != "WAIT" and cfg.single_active and store.active_list(market):
        res["action"], res["setup"] = "WAIT", None
        res["reasons"].append(f"one active signal per symbol: {market} already has one open")
    if res["action"] != "WAIT":
        stop = store.halted(now_ts, cfg.max_daily_losses, daily_loss_limit_r(cfg))
        ok, why = (False, stop) if stop else store.open(market, res["setup"], now_ts)
        if not ok:
            res["action"], res["setup"] = "WAIT", None
            res["reasons"].append(why)
        else:
            res["id"] = f"{market}-{res['setup']['side']}-{res['setup']['bar_time']}"
    return res
