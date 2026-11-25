"""Conflict check for the alert-only detectors (SMC Prime, ICT Session Scalper, Order Flow Pulse). INFORMATION ONLY.
A signal that contradicts the bigger picture is still shown, but with a clear 'CONFLICT' warning so it is never read as a clean entry.
Checks: (1) direction against the H1 trend, (2) an opposing entry zone (the bot's own zones report) that sits on the way to TP1,
(3) Order Flow Pulse is only an estimate from candle volume. Nothing here is a trade decision."""
from __future__ import annotations


def _zones(market, zones_latest, now_ts, max_age=1800):
    rec = (zones_latest or {}).get(market)
    if not rec or (now_ts and now_ts - rec.get("ts", 0) > max_age):
        return []
    return [o for o in rec.get("obs", []) if o.get("zone")]


def conflicts(market, ev, h1_trend, zones_latest=None, now_ts=None):
    """ev: a SIGNAL event of session_alerts. Returns a list of short English warning lines (empty = no conflict found)."""
    out = []
    long = ev["direction"] == "long"
    entry, tp1 = ev["entry"], ev["tp1"]
    if h1_trend and (h1_trend == "down" if long else h1_trend == "up"):
        out.append(f"{ev['direction'].upper()} is against the H1 trend ({h1_trend})")
    lo_p, hi_p = min(entry, tp1), max(entry, tp1)
    for o in _zones(market, zones_latest, now_ts):
        opposite = (o["side"] == "sell") if long else (o["side"] == "buy")
        if not opposite:
            continue
        z = o["zone"]
        if z["high"] >= lo_p and z["low"] <= hi_p:
            kind = "SELL" if long else "BUY"
            out.append(f"an opposing {kind} zone {z['low']:g} - {z['high']:g} sits on the way to TP1 ({tp1:g})")
    if "Pulse" in ev["name"]:
        out.append("Order Flow Pulse is an ESTIMATE from candle volume, not real order flow")
    return out


def warning_text(market, ev, h1_trend, zones_latest=None, now_ts=None):
    """The text to append to the alert; empty when there is nothing to warn about. Only a real conflict gets the strong wording."""
    found = conflicts(market, ev, h1_trend, zones_latest, now_ts)
    real = [c for c in found if "ESTIMATE" not in c]
    if not found:
        return ""
    head = "⛔ CONFLICT: do not treat this as a clean entry. Wait for a closed M15 candle beyond the opposing zone / with the H1 trend." \
        if real else "ℹ️ Note:"
    return head + "\n" + "\n".join("- " + c for c in found)
