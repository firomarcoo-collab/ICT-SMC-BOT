"""Three alert-only detectors (signals only, never orders). Closed candles only; the last bar passed in has just closed.
  1. ICT Session Scalper (M5, New York 10:00-11:00 by default): liquidity sweep -> BOS -> FVG, entry at the FVG midpoint (CE).
  2. SMC Prime (M15 + H1): needs 2+ of {liquidity sweep, CHoCH/BOS, price in an untested OB/FVG} AND the H1 trend.
  3. Order Flow Pulse (M5, ESTIMATE from candle volume, not real order flow): volume >= 1.8 x SMA20[1] and body >= 60% of the range.
Each detector returns dicts: name, symbol, direction, entry, sl, tp1, tp2, rr, bar_time, reason (+ event, zone...).
Every threshold is in Config. Per market state is kept in a dict (dedup keys, pending setups); a restart forgets pending ones."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import json

import displacement_engine as de
import session_tracker
import stop_engine


@dataclass(frozen=True)
class Config:
    tz: str = "America/New_York"
    # --- ICT Session Scalper
    scalper_window: tuple = (10.0, 11.0)       # Silver Bullet window, New York hours
    scalper_session_from: float = 2.0          # the session range (liquidity) starts at this hour
    scalper_lookback: int = 12                 # the sweep happened at most this many M5 bars before the BOS bar
    structure_bars: int = 6                    # bars before the sweep that define the structure high / low to break
    entry_mode: str = "ce"                     # "ce" = FVG midpoint, "edge" = the FVG boundary price touches first
    min_rr: float = 2.0                        # TP1 must pay at least this R:R from the structural stop
    # --- SMC Prime
    smc_lookback: int = 10                     # M15 bars back for the sweep
    smc_min_confluence: int = 2
    smc_tp1_r: float = 2.0
    smc_chase_atr: float = 1.5                 # price already this far above/below the entry: stale
    # --- Order Flow Pulse (estimate)
    pulse_vol_mult: float = 1.8
    pulse_body: float = 0.60
    pulse_sl_mode: str = "low"                 # "low" = beyond the pulse candle, "mid" = its midpoint (fast scalps)
    pulse_tp_atr: tuple = (1.0, 2.0)
    pulse_min_rr: float = 1.0
    pulse_cooldown_bars: int = 6
    pulse_confirm_bars: int = 3                # a pulse this recent confirms the other two alerts
    vol_sma: int = 20
    pulse_range_proxy: bool = True             # feeds without volume: a candle-range spike stands in (labelled 'range proxy')
    swing_k: int = 2
    pool_bars: int = 80
    atr_period: int = 14


CFG = Config()
_STATE: dict = {}


def enabled(market):
    return os.environ.get("SESSION_ALERTS", "1") != "0"


EVENTS = {}          # alert id -> its SIGNAL event (read by conflict_check in the bot loop)


def on(name):
    """SMC Prime, ICT Session Scalper and Order Flow Pulse are all on by default (the user asked for their alerts and tones).
    They stay alert-only, obey the session table and the one-signal-per-market rule, and each can be switched off: SCALPER=0, PULSE=0, SMC_PRIME=0."""
    return os.environ.get(name, "1") != "0"


def hour(t, tz):
    h = t.astimezone(ZoneInfo(tz))
    return h.hour + h.minute / 60.0


def swings(bars, k):
    hi, lo = [], []
    for i in range(k, len(bars) - k):
        w = bars[i - k:i + k + 1]
        if bars[i]["high"] == max(b["high"] for b in w):
            hi.append((i, bars[i]["high"]))
        if bars[i]["low"] == min(b["low"] for b in w):
            lo.append((i, bars[i]["low"]))
    return hi, lo


def fvg_list(bars, start):
    """3-candle gaps whose last candle index >= start: [(i, low_edge, high_edge, 'long'|'short')]."""
    out = []
    for i in range(max(2, start), len(bars)):
        a, c = bars[i - 2], bars[i]
        if c["low"] > a["high"]:
            out.append((i, a["high"], c["low"], "long"))
        elif c["high"] < a["low"]:
            out.append((i, c["high"], a["low"], "short"))
    return out


def vol_ratio(bars, n):
    prev = bars[-1 - n:-1]
    if len(prev) < n:
        return None
    avg = sum(b.get("volume") or 0 for b in prev) / n
    return (bars[-1].get("volume") or 0) / avg if avg > 0 else None


def range_ratio(bars, n):
    prev = bars[-1 - n:-1]
    if len(prev) < n:
        return None
    avg = sum(b["high"] - b["low"] for b in prev) / n
    return (bars[-1]["high"] - bars[-1]["low"]) / avg if avg > 0 else None


def pulse_strength(bars, cfg=CFG):
    """(ratio, proxy): the volume ratio, or the range ratio on a feed without volume (proxy=True). None when neither exists."""
    r = vol_ratio(bars, cfg.vol_sma)
    if r is not None:
        return r, False
    if cfg.pulse_range_proxy and not any((b.get("volume") or 0) for b in bars[-cfg.vol_sma - 1:]):
        r = range_ratio(bars, cfg.vol_sma)
        return (r, True) if r is not None else None
    return None


def pulse_ok(bars, cfg=CFG):
    """The last bar is an Order Flow Pulse (ESTIMATE): returns 'long' / 'short' / None."""
    c = bars[-1]
    rng = c["high"] - c["low"]
    st = pulse_strength(bars, cfg)
    if rng <= 0 or st is None or st[0] < cfg.pulse_vol_mult or abs(c["close"] - c["open"]) / rng < cfg.pulse_body:
        return None
    return "long" if c["close"] > c["open"] else "short" if c["close"] < c["open"] else None


def pulse_recent(direction, bars, cfg=CFG):
    """Was there a pulse in this direction within the last pulse_confirm_bars closed M5 bars? (used as a confirmation line)"""
    return any(pulse_ok(bars[:len(bars) - k], cfg) == direction for k in range(cfg.pulse_confirm_bars) if len(bars) - k > cfg.vol_sma + 1)


def _plan(name, symbol, direction, entry, sl, tp1, tp2, bar_time, reason, **extra):
    risk = abs(entry - sl)
    return dict(name=name, symbol=symbol, direction=direction, entry=entry, sl=sl, tp1=tp1, tp2=tp2,
                rr=abs(tp1 - entry) / risk if risk else 0.0, bar_time=bar_time, reason=reason, event="SIGNAL", **extra)


def _ev(name, event, symbol, direction, bar_time, reason):
    return dict(name=name, event=event, symbol=symbol, direction=direction, bar_time=bar_time, reason=reason)


# ---------------------------------------------------------------- 1. ICT Session Scalper
def scalper(symbol, m5, cfg=CFG, st=None):
    st = _STATE.setdefault(symbol, {}) if st is None else st
    out = []
    if len(m5) < 40:
        return out
    c, t = m5[-1], m5[-1]["time"]
    x = hour(t, cfg.tz)
    a, b = cfg.scalper_window
    in_win = a <= x < b
    iso = t.isoformat()
    keep = []
    for p in st.setdefault("scalp_pending", []):                 # invalidation / expiry of earlier signals
        long = p["direction"] == "long"
        touched = c["low"] <= p["entry"] if long else c["high"] >= p["entry"]
        gone = c["close"] < p["lo"] if long else c["close"] > p["hi"]
        if gone:
            out.append(_ev("ICT Session Scalper", "INVALIDATED", symbol, p["direction"], iso, "a candle closed through the whole FVG: the idea failed"))
        elif touched:
            continue                                                               # the entry was reached: no longer pending
        elif not in_win:
            out.append(_ev("ICT Session Scalper", "MISSED", symbol, p["direction"], iso, f"the window ended at {b:g}:00 New York without a touch"))
        else:
            keep.append(p)
    st["scalp_pending"] = keep
    if not in_win:
        return out
    atr = de.atr_at(m5, cfg.atr_period)
    if not atr:
        return out
    local = lambda bar: bar["time"].astimezone(ZoneInfo(cfg.tz))                   # noqa: E731
    day = local(c).date()
    pre = [bar for bar in m5 if local(bar).date() == day and cfg.scalper_session_from <= hour(bar["time"], cfg.tz) < a]
    base = [bar for bar in m5 if bar["time"] < (pre[-1]["time"] + timedelta(minutes=5) if pre else t)][-cfg.pool_bars:]
    sh, sl_ = swings(base, cfg.swing_k)
    highs = sorted({p for _, p in sh} | ({max(bar["high"] for bar in pre)} if pre else set()))
    lows = sorted({p for _, p in sl_} | ({min(bar["low"] for bar in pre)} if pre else set()))
    j = len(m5) - 1
    for direction in ("long", "short"):
        key = f"{day}-{direction}"
        if key in st.setdefault("scalp_sent", set()):
            continue
        long = direction == "long"
        for s in range(max(cfg.structure_bars, j - cfg.scalper_lookback), j):
            sb = m5[s]
            pools = lows if long else highs
            hit = [p for p in pools if (sb["low"] < p < sb["close"] if long else sb["close"] < p < sb["high"])]
            if not hit:
                continue
            seg = m5[s - cfg.structure_bars:s + 1]
            level = max(bar["high"] for bar in seg) if long else min(bar["low"] for bar in seg)
            bos = (c["close"] > level and m5[j - 1]["close"] <= level) if long else (c["close"] < level and m5[j - 1]["close"] >= level)
            if not bos:
                continue
            gaps = [g for g in fvg_list(m5, s + 1) if g[3] == direction and g[0] <= j]
            if not gaps:
                continue
            _, lo, hi, _ = gaps[-1]
            if (long and c["close"] <= lo) or (not long and c["close"] >= hi):
                continue                                                           # the gap is already closed
            entry = (lo + hi) / 2 if cfg.entry_mode == "ce" else (hi if long else lo)
            extreme = sb["low"] if long else sb["high"]
            buf = stop_engine.buffer(stop_engine.classify(symbol), atr, 0.0)
            sl = extreme - buf if long else extreme + buf
            risk = abs(entry - sl)
            targets = sorted(p for p in (highs if long else lows) if (p > entry if long else p < entry))
            targets = targets if long else targets[::-1]
            if risk <= 0 or not targets:
                continue
            tp1 = targets[0]
            tp2 = targets[1] if len(targets) > 1 else (entry + 3 * risk if long else entry - 3 * risk)
            rr = abs(tp1 - entry) / risk
            if rr < cfg.min_rr:
                st["scalp_sent"].add(key)
                out.append(_ev("ICT Session Scalper", "REJECTED", symbol, direction, iso, f"R:R to the nearest liquidity is 1:{rr:.2f}, below 1:{cfg.min_rr:g}"))
                break
            st["scalp_sent"].add(key)
            ev = _plan("ICT Session Scalper", symbol, direction, entry, sl, tp1, tp2, iso,
                       "liquidity sweep, then BOS, then an FVG inside the same impulse", zone=[lo, hi], sweep_extreme=extreme,
                       invalidation="the FVG closed through before the touch, or 11:00 New York passes without a touch",
                       pulse=pulse_recent(direction, m5, cfg))
            out.append(ev)
            st["scalp_pending"].append({"direction": direction, "entry": entry, "lo": lo, "hi": hi})
            break
    return out


# ---------------------------------------------------------------- 2. SMC Prime
def smc_prime(symbol, m15, h1, h1_trend, cfg=CFG, st=None, m5=None):
    st = _STATE.setdefault(symbol, {}) if st is None else st
    out = []
    if len(m15) < 40 or h1_trend not in ("up", "down"):
        return out
    atr = de.atr_at(m15, cfg.atr_period)
    if not atr:
        return out
    j = len(m15) - 1
    c = m15[j]
    sh, sl_ = swings(m15[:-1], cfg.swing_k)
    for direction in ("long", "short"):
        long = direction == "long"
        if (h1_trend == "up") != long:
            continue
        def swept(s):
            sb = m15[s]
            pools = [p for i, p in (sl_ if long else sh) if i <= s - 1]
            return [p for p in pools if (sb["low"] < p < sb["close"] if long else sb["close"] < p < sb["high"])]

        # bars that swept liquidity first (the most recent first), then the others
        order = sorted(range(max(cfg.swing_k + 2, j - cfg.smc_lookback), j), key=lambda x: (not swept(x), -x))
        for s in order:
            sb = m15[s]
            hit = swept(s)
            seg = m15[max(0, s - 6):s + 1]
            level = max(bar["high"] for bar in seg) if long else min(bar["low"] for bar in seg)
            brk = [i for i in range(s + 1, j + 1) if (m15[i]["close"] > level if long else m15[i]["close"] < level)]
            if not brk:
                continue
            b = brk[0]
            ob, ob_i = None, None
            for i in range(b - 1, s - 1, -1):                                       # last opposite candle before the impulse
                bar = m15[i]
                if (bar["close"] < bar["open"]) if long else (bar["close"] > bar["open"]):
                    ob, ob_i = (bar["low"], bar["high"]), i
                    break
            if ob is None:
                continue
            lo, hi = ob
            if (long and c["close"] < lo) or (not long and c["close"] > hi):
                continue                                                           # already broken through
            untested = all((m15[i]["low"] > hi) if long else (m15[i]["high"] < lo) for i in range(b + 1, j)) if b + 1 < j else True
            in_zone = untested and ((c["low"] <= hi and c["close"] >= lo) if long else (c["high"] >= lo and c["close"] <= hi))
            factors = [n for n, ok in (("liquidity sweep", bool(hit)), ("CHoCH/BOS", True), ("price in an untested OB/FVG", in_zone)) if ok]
            if len(factors) < cfg.smc_min_confluence:
                continue
            key = f"{direction}-{m15[ob_i]['time'].isoformat()}"          # one alert per order block
            if key in st.setdefault("smc_sent", set()):
                continue
            entry = hi if long else lo
            if abs(c["close"] - entry) > cfg.smc_chase_atr * atr and ((c["close"] > entry) if long else (c["close"] < entry)):
                continue                                                           # ran away: stale
            extreme = min(sb["low"], lo) if long else max(sb["high"], hi)
            buf = stop_engine.buffer(stop_engine.classify(symbol), atr, 0.0)
            sl = extreme - buf if long else extreme + buf
            risk = abs(entry - sl)
            if risk <= 0 or (long and sl >= entry) or (not long and sl <= entry):
                continue
            tp1 = entry + cfg.smc_tp1_r * risk if long else entry - cfg.smc_tp1_r * risk
            hsh, hsl = swings(h1[-cfg.pool_bars:], cfg.swing_k) if h1 else ([], [])
            htf = sorted(p for _, p in hsh if p > tp1) if long else sorted((p for _, p in hsl if p < tp1), reverse=True)
            tp2 = htf[0] if htf else (entry + 3 * risk if long else entry - 3 * risk)
            st["smc_sent"].add(key)
            out.append(_plan("SMC Prime", symbol, direction, entry, sl, tp1, tp2, c["time"].isoformat(),
                             " + ".join(factors) + f" | H1 trend {h1_trend}", zone=[lo, hi], confluence=len(factors),
                             invalidation=f"a full M15 close beyond the zone edge {lo if long else hi:g}",
                             actions={"tp1": "move the stop to break-even", "tp2": "next H1 liquidity"},
                             pulse=pulse_recent(direction, m5, cfg) if m5 else False))
            return out
    return out


# ---------------------------------------------------------------- 3. Order Flow Pulse (estimate)
def pulse(symbol, m5, cfg=CFG, st=None):
    st = _STATE.setdefault(symbol, {}) if st is None else st
    out = []
    if len(m5) < cfg.vol_sma + 3:
        return out
    c, prev = m5[-1], m5[-2]
    iso = c["time"].isoformat()
    pend = st.get("pulse_pending")
    if pend:                                                                       # invalidation of the last pulse
        long = pend["direction"] == "long"
        engulf = c["close"] < pend["open"] if long else c["close"] > pend["open"]
        inside = c["close"] <= pend["prev_edge"] if long else c["close"] >= pend["prev_edge"]
        if engulf or inside:
            out.append(_ev("Order Flow Pulse (estimate)", "INVALIDATED", symbol, pend["direction"], iso,
                           "the next candle closed with an opposite engulfing or back inside the pre-pulse range"))
        st["pulse_pending"] = None                                                  # only the very next bar can invalidate
    d = pulse_ok(m5, cfg)
    if not d:
        return out
    n = st.setdefault("pulse_n", 0)
    last = st.get("pulse_last", {}).get(d)
    if last is not None and (c["time"] - datetime.fromisoformat(last)).total_seconds() < cfg.pulse_cooldown_bars * 300:
        return out
    atr = de.atr_at(m5, cfg.atr_period)
    if not atr:
        return out
    strength = pulse_strength(m5, cfg)
    long = d == "long"
    entry = c["close"]
    sl = (c["low"] if cfg.pulse_sl_mode == "low" else (c["high"] + c["low"]) / 2) if long else \
         (c["high"] if cfg.pulse_sl_mode == "low" else (c["high"] + c["low"]) / 2)
    sl = sl - 0.1 * atr if long else sl + 0.1 * atr
    risk = abs(entry - sl)
    r1, r2 = max(cfg.pulse_tp_atr[0] * atr, risk), max(cfg.pulse_tp_atr[1] * atr, 2 * risk)        # ATR multiple, never below 1R / 2R
    tp1, tp2 = (entry + r1, entry + r2) if long else (entry - r1, entry - r2)
    if risk <= 0 or abs(tp1 - entry) / risk < cfg.pulse_min_rr:
        return out
    st.setdefault("pulse_last", {})[d] = iso
    st["pulse_pending"] = {"direction": d, "open": c["open"], "prev_edge": prev["high"] if long else prev["low"]}
    st["pulse_n"] = n + 1
    ev = _plan("Order Flow Pulse (estimate)", symbol, d, entry, sl, tp1, tp2, iso,
               (f"range {strength[0]:.1f}x the 20-bar average (no volume on this feed: a range proxy)" if strength[1]
                else f"volume {strength[0]:.1f}x the 20-bar average") + f", body {abs(c['close'] - c['open']) / (c['high'] - c['low']):.0%} of the range",
               proxy=strength[1])
    out.append(ev)
    return out


# ---------------------------------------------------------------- phone / screen text
def fib_gate(ev, m15):
    """Fibonacci check for a module SIGNAL: is the entry inside the golden zone (50-79%, FIB_LO / FIB_HI) of the latest M15 leg in the
    trade's direction? The result travels with the signal (fib_ok True / False / None = no clear leg, plus the text). It no longer drops
    the signal: the unified engine requires it, except on a strong confirmation where it may be waived. FIB_GATE=0 switches the check off."""
    if ev.get("event") != "SIGNAL" or os.environ.get("FIB_GATE", "1") == "0":
        return ev
    import selective
    try:
        lo, hi = float(os.environ.get("FIB_LO") or 0.50), float(os.environ.get("FIB_HI") or 0.79)
    except ValueError:
        lo, hi = 0.50, 0.79
    side = "buy" if ev["direction"] == "long" else "sell"
    try:
        inside, text = selective.fib_check(m15 or [], side, ev["entry"], lo, hi)
    except Exception:                                                              # noqa: BLE001
        inside, text = None, "Fib: could not be computed"
    return dict(ev, fib_ok=inside, fib=text)


def render(ev, digits=2):
    f = lambda v: f"{v:.{digits}f}"                                                # noqa: E731
    name, kind = ev["name"], ev["event"]
    head = f"{ev['symbol']} | {ev['direction'].upper()} | {name}"
    foot = "⚠️ Signal only: place the trade yourself in MT5. No win rate is guaranteed."
    if kind == "SIGNAL":
        est = "\n(ESTIMATE from candle volume, not real order flow.)" if "Pulse" in name else ""
        lines = [f"🟢 [{name.upper()}] — {head}", f"Why: {ev['reason']}", "📌 Trade plan:",
                 f"- Entry: {f(ev['entry'])}" + (" (FVG midpoint)" if name == "ICT Session Scalper" else ""),
                 f"- Hard SL: {f(ev['sl'])}", f"- TP1 {f(ev['tp1'])}", f"- TP2 {f(ev['tp2'])}", f"- R:R to TP1: 1:{ev['rr']:.2f}"]
        if ev.get("zone"):
            lines.append(f"- Zone: {f(ev['zone'][0])} - {f(ev['zone'][1])}")
        if ev.get("invalidation"):
            lines.append(f"- Invalidation: {ev['invalidation']}")
        if ev.get("actions"):
            lines.append(f"- TP1: {ev['actions']['tp1']}; TP2: {ev['actions']['tp2']}")
        if ev.get("fib"):
            lines.append(("📐 " if ev.get("fib_ok") else "⚠️ ") + ev["fib"]
                         + ("" if ev.get("fib_ok") else " - not confirmed: only a strong unified confirmation may waive this"))
        if ev.get("pulse") is True:
            lines.append("✅ Order Flow Pulse (estimate) confirms the direction.")
        if est:
            lines.append(est.strip())
        if ev.get("proxy"):
            lines.append("ℹ️ No volume on this feed: a candle-range spike was used as the proxy.")
        lines.append(foot)
        return "\n".join(lines)
    icon = {"INVALIDATED": "🔴", "MISSED": "⚪", "REJECTED": "⚪"}.get(kind, "⚪")
    return f"{icon} [{kind}] — {head}\nReason: {ev['reason']}"


CLASS = {("ICT Session Scalper", "SIGNAL"): "SCALP_SIGNAL", ("ICT Session Scalper", "INVALIDATED"): "SCALP_INVALIDATED",
         ("SMC Prime", "SIGNAL"): "SMC_SIGNAL", ("Order Flow Pulse (estimate)", "SIGNAL"): "PULSE_SIGNAL",
         ("Order Flow Pulse (estimate)", "INVALIDATED"): "PULSE_INVALIDATED"}


STATE_KEYS = ("scalp_pending", "scalp_sent", "smc_sent", "pulse_last", "pulse_pending")
_LOADED = False


def state_file():
    return os.path.join(os.environ.get("SESSION_DIR", "."), "session_state.json")


def load_state():
    """Pending setups, sent keys and cooldowns survive a restart (session_state.json)."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        with open(state_file()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    for market, d in data.items():
        st = _STATE.setdefault(market, {})
        for k in STATE_KEYS:
            if k in d:
                st[k] = set(d[k]) if k in ("scalp_sent", "smc_sent") else d[k]


def save_state():
    data = {m: {k: (sorted(v) if isinstance(v, set) else v) for k, v in st.items() if k in STATE_KEYS} for m, st in _STATE.items()}
    try:
        with open(state_file(), "w") as f:
            json.dump(data, f)
    except OSError:
        pass


def process(market, m5, m15, h1, h1_trend, digits=2, cfg=CFG):
    """[(class, sid, text)] for the newest closed bars. Classes not in CLASS (MISSED / REJECTED) are shown on screen only."""
    load_state()
    st = _STATE.setdefault(market, {})
    out = []
    if m5:                                                                          # measure the earlier signals first
        for r in session_tracker.update(market, m5):
            try:                                                                    # the idea ended: the market may signal again
                import signal_manager
                signal_manager.release(market, {"SMC Prime": "SMC_SIGNAL", "ICT Session Scalper": "SCALP_SIGNAL",
                                                "Order Flow Pulse": "PULSE_SIGNAL"}.get(r["name"]))
            except Exception:                                                       # noqa: BLE001
                pass
            if r["result"] != "NOFILL":
                out.append(("SESSION_INFO", f"{market}-RESULT-{r['time']}", session_tracker.line(r)))
    jobs = []
    if on("SCALPER") and m5:
        jobs.append(("scalper", m5, lambda: scalper(market, m5, cfg, st)))
    if on("SMC_PRIME") and m15:
        jobs.append(("smc", m15, lambda: smc_prime(market, m15, h1, h1_trend, cfg, st, m5)))
    if on("PULSE") and m5:
        jobs.append(("pulse", m5, lambda: pulse(market, m5, cfg, st)))
    for key, bars, fn in jobs:
        t = bars[-1]["time"]
        if st.get("seen_" + key) == t:                                             # idempotent: one pass per closed bar
            continue
        st["seen_" + key] = t
        for ev in fn():
            ev = fib_gate(ev, m15)
            cls = CLASS.get((ev["name"], ev["event"]), "SESSION_INFO")
            if ev["event"] == "SIGNAL":
                session_tracker.open_signal(market, ev)
            EVENTS[f"{market}-{cls}-{ev['bar_time']}"] = ev
            out.append((cls, f"{market}-{cls}-{ev['bar_time']}", render(ev, digits)))
    save_state()
    return out
