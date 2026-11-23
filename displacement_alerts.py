"""Phone / screen alerts for displacement_engine.py (signals only, English). One engine per market, kept between scans.
Only the newest closed M15 bar can produce an alert: on the first scan (or after a restart) the engine silently replays the
last bars to rebuild its state, so old events are never announced again. DISPLACEMENT=0 turns it off.
Alert-only: these signals are not opened in the signal store / tracker, so the trades report does not count them."""
from __future__ import annotations

import os

import displacement_engine as de

REPLAY_BARS = 120
_ENGINES: dict = {}


def enabled(market: str) -> bool:
    return os.environ.get("DISPLACEMENT", "1") != "0"


def has_volume(bars) -> bool:
    return sum((b.get("volume") or 0) for b in bars[-30:]) > 0


def engine_for(market, m15):
    """One engine per market. Feeds without volume (Yahoo FX) skip the volume filter and say so in the alert."""
    eng = _ENGINES.get(market)
    vol = has_volume(m15)
    if eng is None or eng.cfg.volume_required != vol:
        old = eng
        eng = de.Engine(market, de.Config(volume_required=vol))
        if old is not None:                      # volume availability changed: rebuild silently from the bars
            eng.last_ts = None
        _ENGINES[market] = eng
    return eng


def _f(v, digits):
    return f"{v:.{digits}f}"


def lot_line(market, ev):
    """Lot for the confirmed plan from BALANCE and the verified specs in markets.json (the Hard SL alone sizes it; 50% risk when
    against the bias). Says exactly what is missing instead of guessing."""
    try:
        bal = float(os.environ.get("BALANCE") or 0)
    except ValueError:
        bal = 0.0
    if bal <= 0:
        return "- Lot: set BALANCE (your account in $) to see the lot size."
    try:
        import markets
        spec = markets.load_markets().get(markets.canonical(market))
        if spec is None:
            return "- Lot: this symbol has no spec in markets.json."
        pct = min(float(os.environ.get("RISK_PCT") or spec.risk_percent), 2.0) * ev["risk_scale"]
        lot, money, why = markets.lot_for_risk(spec, bal, pct, ev["entry"], ev["sl"])
    except Exception as e:                                                          # noqa: BLE001
        return f"- Lot: could not be calculated ({e})."
    return (f"- Lot: {lot:g} (risk {pct:g}% = ${money:.2f} at the Hard SL)" if lot > 0 else f"- Lot: not sized ({why}). Fill the specs with set_spec.py.")


def render(ev, digits=2, no_volume=False):
    f = lambda v: _f(v, digits)                                                            # noqa: E731
    d = ev.get("direction", "").upper()
    head = f"{ev['symbol']} | {d} | DISPLACEMENT" + (f" ({ev['mode']})" if ev.get("mode") else "")
    foot = "⚠️ Signal only: place the trade yourself in MT5. No win rate is guaranteed."
    note = "ℹ️ No volume data on this feed: the volume filter was skipped." if no_volume else ""
    kind = ev["event"]
    if kind == "SETUP":
        lines = [f"🟡 [SETUP ONLY - wait for the retest and the confirmation] — {head}",
                 f"Liquidity: {ev['pool']} | impulse close {f(ev['impulse_close'])} | FVG {f(ev['fvg_zone'][0])} - {f(ev['fvg_zone'][1])}",
                 f"Invalidation: two M15 closes beyond {f(ev['invalidation'])}",
                 "Do not chase: if price runs 1.2 ATR without a retest the setup is MISSED."]
        if ev.get("counter_trend"):
            lines.append("Against the H1 bias (reversal): risk 50% of normal.")
    elif kind == "RETEST":
        lines = [f"🟤 [RETEST] — {head}", f"Price is back in the zone, retest extreme {f(ev['retest_extreme'])}.",
                 "Wait for a closed M15 candle in the direction that reclaims the zone midpoint. No entry yet."]
    elif kind == "CONFIRMED":
        r = ev["r"]
        lines = [f"🟢 [CONFIRMED ENTRY] — {head}", "📌 Trade plan:",
                 f"- Entry: {f(ev['entry'])} (the closed M15 confirmation candle)",
                 f"- Hard SL: {f(ev['sl'])} (retest extreme -/+ 0.5 ATR) | 1R = {f(r)}",
                 f"- Early invalidation: an M15 close beyond {f(ev['early_invalidation'])}",
                 f"- TP1 {f(ev['tp1'])} (1R: move the stop to break-even)", f"- TP2 {f(ev['tp2'])} (2R: close 50%)",
                 f"- TP3 {f(ev['tp3'])} (3R or the opposing HTF pool)",
                 f"- Risk size: {int(ev['risk_scale'] * 100)}% of your normal risk" + (" (reversal against the H1 bias)" if ev["counter_trend"] else ""),
                 lot_line(ev["symbol"], ev)]
    elif kind == "INVALIDATED":
        lines = [f"🔴 [SETUP INVALIDATED] — {head}", f"Reason: {ev['reason']}"]
    else:
        lines = [f"⚪ [MISSED] — {head}", f"Reason: {ev['reason']}"]
    if note:
        lines.append(note)
    lines.append(foot)
    return "\n".join(lines)


CLASS = {"SETUP": "DISP_SETUP", "RETEST": "DISP_RETEST", "CONFIRMED": "DISP_SIGNAL", "INVALIDATED": "DISP_INVALIDATED"}


def process(market, m15, h1, digits=2):
    """[(class, sid, text)] for events of the newest closed M15 bar. MISSED is shown on screen only (class 'DISP_MISSED')."""
    if not m15 or len(m15) < 30:
        return []
    eng = engine_for(market, m15)
    nv = not eng.cfg.volume_required
    fresh = {b["time"].isoformat() for b in m15[-1:]}
    out = []
    start = max(1, len(m15) - REPLAY_BARS)
    for i in range(start, len(m15) + 1):
        bars = m15[:i]
        if eng.last_ts is not None and bars[-1]["time"] <= eng.last_ts:
            continue
        for ev in eng.on_bar(bars, h1):
            if ev["bar_time"] in fresh:
                cls = CLASS.get(ev["event"], "DISP_MISSED")
                out.append((cls, f"{market}-DISP-{ev['event']}-{ev['bar_time']}", render(ev, digits, nv)))
    return out
