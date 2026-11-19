"""Reversal state machine, one per market and direction (analysis only; the only trade signal is FINAL, which the normal
signal path issues after every mandatory gate and a score of 70+, a technical rating and NOT a win probability).

Stages, always in this order (a stage is never skipped, only passed through in one scan):
  0 SWEEP DETECTED                    a liquidity sweep (a wick alone is not one; a CHoCH without displacement is not enough)
  1 MSS CONFIRMED                     close-based structure break after the sweep, with displacement
  2 DISPLACEMENT CONFIRMED            the displacement candle exists and the FVG / OB it created is still being awaited
  3 WAITING FOR FVG/OB RETEST         a valid, unconsumed FVG / OB exists, price has not reached it
  4 WAITING FOR CANDLE CONFIRMATION   price is in the zone: wait for the closed M5 confirmation candle
  5 FINAL BUY / FINAL SELL            the signal was issued (only by the engine)
Ends: INVALIDATED (price broke the structure invalidation level), EXPIRED (too old), SUPERSEDED (a better setup of the same
side), and a setup of the other side is SUPPRESSED while this one leads (no BUY and SELL at the same time on one market).
"""
import os

STAGES = ("SWEEP DETECTED", "MSS CONFIRMED", "DISPLACEMENT CONFIRMED", "WAITING FOR FVG/OB RETEST",
          "WAITING FOR CANDLE CONFIRMATION", "FINAL")
FINAL = 5
BAR = 900                                                   # M15 candle in seconds


def wait_mss_bars():
    """A sweep that gets no MSS within this many M15 candles is dropped (env WAIT_MSS_BARS, default 12 = 3 hours)."""
    return int(float(os.environ.get("WAIT_MSS_BARS") or 12))


def max_bars():
    """Any setup older than this many M15 candles expires (env SETUP_MAX_BARS, default 32 = 8 hours)."""
    return int(float(os.environ.get("SETUP_MAX_BARS") or 32))


def stage_name(idx, side):
    return f"FINAL {side.upper()}" if idx == FINAL else STAGES[idx]


def target_stage(obs):
    """The stage the market evidence supports right now for one observation (see alert_classes / watch_observations)."""
    if not obs.get("stage3"):
        return 0                                           # sweep only (a CHoCH without displacement stays here)
    if not obs.get("zone"):
        return 2                                           # MSS with displacement, the zone is not there yet
    return 4 if obs.get("rel") == "in" else 3


def _sweep_time(sid):
    try:
        return int(str(sid).rsplit("-", 1)[-1])
    except ValueError:
        return 0


def _rec(state, sid):
    return state.setups.get(sid)


def _ensure(state, market, o, now):
    """The persisted record of an observed setup (created on first sight, same shape alert_classes.process uses)."""
    return state.setups.setdefault(o["sid"], {"market": market, "side": o["side"], "extreme": o["extreme"], "zone": None,
                                              "phase": None, "created": int(now), "done": ""})


def arbitrate(state, market, observations, now, htf_trend=None, active_sides=()):
    """One live setup per direction, one direction per market. Returns (kept observations, notes).
    active_sides: sides with an open signal of this market (the opposite side is suppressed while it lives)."""
    notes = []
    best = {}
    for o in observations:
        _ensure(state, market, o, now)
    for o in observations:                                 # newest / most advanced setup of each side
        key = (target_stage(o), _sweep_time(o["sid"]))
        cur = best.get(o["side"])
        if cur is None or key > cur[0]:
            best[o["side"]] = (key, o)
    for o in observations:
        if best[o["side"]][1] is not o:
            r = _rec(state, o["sid"])
            if r and not r.get("done"):
                r["done"] = "superseded"
    live = {side: v[1] for side, v in best.items()}
    keep = dict(live)
    why = {}
    if active_sides:
        for side in list(keep):
            if side not in active_sides:
                why[side] = f"an active {'/'.join(s.upper() for s in sorted(active_sides))} signal is open on {market}"
                del keep[side]
    elif len(keep) == 2:
        def rank(side):
            o = keep[side]
            aligned = 1 if htf_trend and ((htf_trend == "up") == (side == "buy")) and htf_trend in ("up", "down") else 0
            return (target_stage(o), aligned, _sweep_time(o["sid"]))
        lose = min(keep, key=rank)
        win = max(keep, key=rank)
        why[lose] = f"conflicts with the {win.upper()} setup ({stage_name(target_stage(keep[win]), win)})"
        del keep[lose]
    for side, o in live.items():
        r = _rec(state, o["sid"])
        if side in why:
            if r is not None:
                r["suppressed"] = why[side]
            if state.fire(o["sid"], "SUPPRESSED:" + why[side], now):
                notes.append(("SUPPRESSED", o["sid"], f"{market} {side.upper()} setup SUPPRESSED: {why[side]}"))
        elif r is not None:
            r["suppressed"] = ""
    return list(keep.values()), notes


def update(state, market, observations, now, lang="en"):
    """Advances the machine of each observed setup (in order, never skipping) and expires old ones.
    Returns [(kind, sid, text)] where kind is STAGE or EXPIRED. Print-only lines."""
    out = []
    now = int(now)
    for o in observations:
        r = _rec(state, o["sid"])
        if r is None or r.get("done"):
            continue
        cur = r.get("mstage", -1)
        new = target_stage(o)
        if new > cur:
            for i in range(cur + 1, new + 1):
                r.setdefault("history", []).append({"stage": STAGES[i], "t": now})
                out.append(("STAGE", o["sid"], _line(market, o["side"], STAGES[i], lang)))
            r["mstage"] = new
        elif cur in (3, 4) and new in (3, 4) and new != cur:
            r["mstage"] = new                              # price left / re-entered the zone
            r.setdefault("history", []).append({"stage": STAGES[new], "t": now})
            out.append(("STAGE", o["sid"], _line(market, o["side"], STAGES[new], lang)))
    for sid, r in list(state.setups.items()):
        if r.get("market") != market or r.get("done") or r.get("kind") == "IFVG":
            continue
        age = (now - int(r.get("created", now))) / BAR
        if (r.get("mstage", 0) <= 0 and age > wait_mss_bars()) or age > max_bars():
            r["done"] = "expired"
            if not r.get("suppressed"):
                why = (f"no MSS with displacement within {wait_mss_bars()} M15 candles" if r.get("mstage", 0) <= 0
                       else f"older than {max_bars()} M15 candles")
                out.append(("EXPIRED", sid, f"STAGE {market} {r['side'].upper()}: SETUP EXPIRED ({why}). Forget it."
                            if lang != "ar" else f"المرحلة {market} {r['side'].upper()}: انتهت صلاحية الإعداد ({why}). تجاهله."))
    state.save()
    return out


def _line(market, side, stage, lang):
    note = "analysis only, NOT an entry signal" if lang != "ar" else "تحليل فقط وليس إشارة دخول"
    return f"STAGE {market} {side.upper()}: {stage} ({note})"


def mark_final(state, market, side, now):
    """The engine issued a signal: the most advanced live setup of this side reaches FINAL. Returns the stage trail text
    ('SWEEP DETECTED > MSS CONFIRMED > ... > FINAL BUY'). Call BEFORE state.mark_confirmed."""
    cand = [r for r in state.setups.values() if r.get("market") == market and r["side"] == side and not r.get("done")]
    r = max(cand, key=lambda x: (x.get("mstage", -1), x.get("created", 0)), default=None)
    if r is None:
        return f"{stage_name(FINAL, side)} (issued within one scan, the earlier stages were not watched)"
    r["mstage"] = FINAL
    r.setdefault("history", []).append({"stage": stage_name(FINAL, side), "t": int(now)})
    seen, trail = set(), []
    for h in r["history"]:
        if h["stage"] not in seen:
            seen.add(h["stage"])
            trail.append(h["stage"])
    return " > ".join(trail)
