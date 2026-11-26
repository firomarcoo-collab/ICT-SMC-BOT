"""GOLD STRICT profile (opt-in, XAUUSD only, signals only): `GOLD_STRICT=1`. Extra rules on top of the unified confirmation:
  1. Kill zones in Jordan time (Asia/Amman, GMT+3): London 10:00-13:00, New York 16:00-19:00. Any other time = no signal.
  2. Entry levels inside the FVG of the setup: 50% equilibrium and 61.8% depth (levels YOU act on; no entry removes drawdown
     and nothing here promises zero drawdown).
  3. Stop = the bot's structural stop beyond the sweep wick. It is never tightened: a stop wider than GOLD_MAX_SL_PIPS (15) means NO signal.
  4. Lot = balance x risk% / (pips at risk x 10), pips = |entry - stop| x 10 (the standard 100 oz gold contract). Risk = GOLD_RISK_PCT (1.0).
  5. R:R to TP2 of at least 1:GOLD_MIN_RR (5) or no signal. TP1 at 1:2, then close half by hand and move the stop to break-even; TP2 at 1:5 for the rest.
It only reads and judges; the bot never closes, moves or places anything."""
from __future__ import annotations

import os
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:                                               # pragma: no cover
    ZoneInfo = None

KILL_ZONES = (("London", 10, 13), ("New York", 16, 19))           # Jordan local hours [start, end)


def enabled(market):
    return os.environ.get("GOLD_STRICT", "0") == "1" and str(market).upper() in ("XAUUSD", "GOLD")


def _num(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return float(default)


def jordan_time(now_ts):
    tz = ZoneInfo("Asia/Amman") if ZoneInfo else timezone.utc
    return datetime.fromtimestamp(now_ts, tz)


def kill_zone(now_ts):
    """(name or None, text) for the Jordan local time of now_ts."""
    t = jordan_time(now_ts)
    h = t.hour + t.minute / 60
    for name, a, b in KILL_ZONES:
        if a <= h < b:
            return name, f"{name} kill zone ({t:%H:%M} Jordan time)"
    return None, f"outside the kill zones ({t:%H:%M} Jordan time; London 10:00-13:00, New York 16:00-19:00)"


def fvg_levels(zone, side):
    """(50% equilibrium, 61.8% depth) of the FVG for the trade side. Depth is measured from the edge price enters first:
    buy = from the top down, sell = from the bottom up."""
    lo, hi = min(zone), max(zone)
    rng = hi - lo
    if side == "buy":
        return hi - 0.5 * rng, hi - 0.618 * rng
    return lo + 0.5 * rng, lo + 0.618 * rng


def lot_size(balance, risk_pct, entry, sl):
    """Lot = (balance x risk%) / (pips at risk x 10); pips = |entry - sl| x 10. None when it cannot be computed."""
    pips = abs(entry - sl) * 10
    if balance <= 0 or pips <= 0:
        return None
    return round(balance * risk_pct / 100 / (pips * 10), 2)


def plan(market, side, zone, sl, now_ts, balance=0.0, htf_liquidity=None):
    """Strict plan or a refusal. Returns {ok, why (list), ...}. zone = (low, high) of the setup's FVG, sl = the structural stop."""
    out = {"ok": False, "why": [], "market": market, "side": side}
    name, ktext = kill_zone(now_ts)
    out["kill_zone"] = ktext
    if name is None:
        out["why"].append(ktext)
    if not zone:
        out["why"].append("no FVG zone to take the entry levels from")
        return out
    e50, e618 = fvg_levels(zone, side)
    sgn = 1 if side == "buy" else -1
    max_pips = _num("GOLD_MAX_SL_PIPS", 15)
    min_rr = _num("GOLD_MIN_RR", 5)
    out.update(entry_50=e50, entry_618=e618, sl=sl)
    if (e50 - sl) * sgn <= 0 or (e618 - sl) * sgn <= 0:
        out["why"].append("the stop is not beyond the entry levels (price is already through the invalidation)")
        return out
    for lbl, e in (("50%", e50), ("61.8%", e618)):
        pips = abs(e - sl) * 10
        if pips > max_pips:
            out["why"].append(f"stop at {pips:.0f} pips from the {lbl} entry is wider than {max_pips:.0f} pips (the stop is never tightened: skip)")
    risk = abs(e50 - sl)
    tp1, tp2 = e50 + sgn * 2 * risk, e50 + sgn * min_rr * risk
    out.update(tp1=tp1, tp2=tp2, rr_tp1=2.0, rr_tp2=min_rr)
    if htf_liquidity is not None and (htf_liquidity - e50) * sgn < min_rr * risk:
        out["why"].append(f"the next HTF liquidity at {htf_liquidity:.2f} is closer than 1:{min_rr:g}")
    pct = _num("GOLD_RISK_PCT", 1.0)
    out["lot"] = lot_size(balance, pct, e50, sl)
    out["lot_618"] = lot_size(balance, pct, e618, sl)
    out["risk_pct"] = pct
    out["pips_50"] = abs(e50 - sl) * 10
    out["ok"] = not out["why"]
    return out


def format_plan(p, balance=0.0):
    if not p.get("ok"):
        return "GOLD STRICT: no signal - " + "; ".join(p["why"])
    lot = f"{p['lot']:.2f} lot" if p.get("lot") else "set BALANCE to get the lot size"
    return "\n".join([
        "GOLD STRICT PLAN (signal only: you place and manage it)", f"Kill zone: {p['kill_zone']}",
        f"Entry level 1 (50% of the FVG): {p['entry_50']:.2f} | Entry level 2 (61.8%): {p['entry_618']:.2f}",
        f"Stop Loss: {p['sl']:.2f} ({p['pips_50']:.0f} pips from entry 1, beyond the sweep wick; never tightened)",
        f"TP1 (1:2): {p['tp1']:.2f}: close 50% by hand, then move the stop to break-even",
        f"TP2 (1:{p['rr_tp2']:g}): {p['tp2']:.2f}: let the other 50% run",
        f"Risk {p['risk_pct']:g}% of the account -> {lot} (lot = risk / (pips x 10)); the 61.8% entry would be {p['lot_618']:.2f} lot" if p.get("lot_618") else
        f"Risk {p['risk_pct']:g}% of the account -> {lot}",
        "Any entry can still be stopped out: this does not remove drawdown and no win rate is guaranteed."])
