"""UNIFIED TRADE CONFIRMATION ENGINE (signals only: it never places an order).
Three analysis modules vote per market; the engine turns the votes plus the main SELECTIVE chain into ONE decision:
CONFIRMED BUY / CONFIRMED SELL / WAIT / NO TRADE.
  SMC Prime (35)             market structure, order blocks, H1 trend
  Order Flow Pulse (30)      momentum; an ESTIMATE from candle volume, never real order flow
  ICT Session Scalper (35)   liquidity sweep, FVG, session timing
The main SELECTIVE signal is mandatory: it carries the full chain (H4/H1 context, sweep, CHoCH/MSS, displacement, FVG/OB retest, closed M15
confirmation, R:R >= 1:2, spread/news/session filters) and the levels (structural stop). Without it the answer is WAIT.
A module that points the other way is a veto (NO TRADE) whatever the score. The score (min 80) = each module's weight x (checks passed / 3);
it counts conditions, it is not a win probability. One confirmation per main signal id, kept across restarts (UNIFIED_FILE)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

WEIGHTS = {"smc": 35, "pulse": 30, "scalper": 35}
LABEL = {"smc": "SMC Prime", "pulse": "Order Flow Pulse", "scalper": "ICT Session Scalper"}
KEY = {"SMC Prime": "smc", "ICT Session Scalper": "scalper", "Order Flow Pulse (estimate)": "pulse"}
TTL = {"smc": 3 * 3600, "scalper": 2 * 3600, "pulse": 3600}      # how long a module's vote stays alive
MAIN_TTL = 6 * 3600
PULSE_FRESH = 1800                                               # a Pulse older than 30 min earns no 'fresh' check
MIN_SCORE = 80
SIDE = {"long": "buy", "short": "sell", "buy": "buy", "sell": "sell"}


def quiet_modules():
    """The separate module alerts are informational (silent, labelled) unless UNIFIED_QUIET_MODULES=0."""
    return os.environ.get("UNIFIED_QUIET_MODULES", "1") != "0"


def _file():
    return os.environ.get("UNIFIED_FILE", "unified_state.json")


def _load():
    try:
        with open(_file()) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("votes", {})
    d.setdefault("main", {})
    d.setdefault("sent", {})
    return d


def _save(d):
    try:
        with open(_file(), "w") as f:
            json.dump(d, f)
    except OSError:
        pass


def on_event(market, ev, h1_trend, now_ts):
    """A module event from session_alerts. SIGNAL = a live vote; INVALIDATED = the vote is withdrawn."""
    key = KEY.get(ev.get("name"))
    if not key:
        return
    d = _load()
    mv = d["votes"].setdefault(market, {})
    side = SIDE.get(ev.get("direction"))
    if ev.get("event") == "SIGNAL":
        mv[key] = {"side": side, "ts": int(now_ts), "entry": ev["entry"], "sl": ev["sl"], "tp1": ev["tp1"], "tp2": ev["tp2"],
                   "rr": ev.get("rr"), "zone": ev.get("zone"), "proxy": bool(ev.get("proxy")), "h1": h1_trend,
                   "reason": ev.get("reason", ""), "bar_time": ev.get("bar_time")}
    elif ev.get("event") == "INVALIDATED" and mv.get(key) and mv[key]["side"] == side:
        del mv[key]
    else:
        return
    _save(d)


def record_main(market, setup, report, now_ts, sid):
    """The main SELECTIVE chain produced a signal (it already passed score >= 80, R:R >= 1:2, the filters and the closed M15 confirmation)."""
    d = _load()
    d["main"][market] = {"side": setup["side"], "ts": int(now_ts), "id": str(sid or f"{market}-{setup.get('bar_time')}"),
                         "entry": setup["entry"], "sl": setup["sl"], "tp1": setup.get("tp1"), "tp2": setup["tp"],
                         "score100": setup.get("score100"), "trends": setup.get("trends") or {}, "report": report or {},
                         "bar_time": setup.get("bar_time")}
    _save(d)


def _live(d, market, now_ts):
    votes = {}
    for k, v in (d["votes"].get(market) or {}).items():
        if now_ts - v["ts"] <= TTL[k]:
            votes[k] = v
    main = d["main"].get(market)
    if main and now_ts - main["ts"] > MAIN_TTL:
        main = None
    return votes, main


def _checks(key, v, side, now_ts):
    """Three objective checks per module. The first (same direction) is also a veto when it fails."""
    zone = bool(v.get("zone"))
    if key == "smc":
        return [v["side"] == side, v.get("h1") == ("up" if side == "buy" else "down"), zone]
    if key == "scalper":
        return [v["side"] == side, True, zone]                    # it only signals inside its New York windows
    return [v["side"] == side, not v.get("proxy"), now_ts - v["ts"] <= PULSE_FRESH]


def decide(market, now_ts=None, d=None):
    """Pure decision from the stored votes. Returns a dict with decision, score, modules, reasons, main, id."""
    now_ts = int(now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp())
    d = d or _load()
    votes, main = _live(d, market, now_ts)
    mods = {k: (votes[k]["side"].upper() if k in votes else "WAIT") for k in WEIGHTS}
    out = {"market": market, "modules": mods, "main": main, "score": 0.0, "decision": "WAIT", "side": None, "reasons": [], "id": None}
    sides = {v["side"] for v in votes.values()} | ({main["side"]} if main else set())
    if len(sides) > 1:
        out["decision"] = "NO TRADE"
        who = ", ".join(f"{LABEL[k]} {v['side'].upper()}" for k, v in votes.items())
        out["reasons"].append("conflict: " + who + (f", main {main['side'].upper()}" if main else ""))
        return out
    if not main:
        out["reasons"].append("no live signal from the main SELECTIVE chain (structure, sweep, displacement, FVG/OB retest, M15 confirmation)")
        out["side"] = next(iter(sides)) if sides else None
        return out
    side = main["side"]
    out["side"] = side
    missing = [LABEL[k] for k in WEIGHTS if k not in votes]
    if missing:
        out["reasons"].append("waiting for: " + ", ".join(missing))
        return out
    score = 0.0
    for k, v in votes.items():
        ok = _checks(k, v, side, now_ts)
        score += WEIGHTS[k] * sum(ok) / 3
    out["score"] = round(score, 1)
    if score < MIN_SCORE:
        out["reasons"].append(f"score {score:.0f}/100 is below the minimum {MIN_SCORE}")
        return out
    out["decision"] = "CONFIRMED " + side.upper()
    out["id"] = f"{market}-UNI-{side}-{main['id']}"
    out["votes"] = votes
    return out


def _n(v, digits):
    return "-" if v is None else f"{v:.{digits}f}"


def format_alert(res, digits=2):
    m, votes = res["main"], res["votes"]
    zs = [m["entry"]] + [v["entry"] for v in votes.values()]
    rep = m.get("report") or {}
    tr = m.get("trends") or {}
    side = res["side"]
    bias = "BULLISH" if side == "buy" else "BEARISH"
    risk = abs(m["entry"] - m["sl"])
    rr = abs(m["tp2"] - m["entry"]) / risk if risk else 0.0
    est = " (estimate from candle volume)"
    return "\n".join([
        f"KENAN TRADER AI - {res['decision']}", "",
        f"Symbol: {res['market']}", f"Direction: {side.upper()}",
        f"Entry Zone: {_n(min(zs), digits)} - {_n(max(zs), digits)}",
        f"Stop Loss: {_n(m['sl'], digits)}",
        f"Take Profit 1: {_n(m.get('tp1'), digits)} (1R: then move the stop to break-even by hand)",
        f"Take Profit 2: {_n(m['tp2'], digits)}", "",
        f"SMC Prime: CONFIRMED", f"Order Flow Pulse: CONFIRMED{est}", "ICT Session Scalper: CONFIRMED", "",
        f"Confluence Score: {res['score']:.0f}/100", "Timeframe: M15 entry (H4/H1 context)", f"Market Bias: {bias}",
        f"Higher timeframes: H4 {tr.get('h4', '-')} | H1 {tr.get('h1', '-')}",
        f"Liquidity: {rep.get('liquidity') or '-'}", f"Structure: {rep.get('structure') or '-'}",
        f"Displacement: {rep.get('reversal') or '-'}", f"FVG/OB Retest: {rep.get('retest') or '-'}",
        f"Confirmation: {rep.get('confirm') or '-'}", "",
        f"Risk/Reward to TP2: 1:{rr:.1f} | Risk per trade: 0.5% of the account",
        "Status: VALID TRADE SIGNAL", "Signal only: place the trade yourself in MT5. No win rate is guaranteed."])


def run_market(market, now_ts, digits=2):
    """Called after every scan of a market. Returns an alert dict once per main signal when the decision is CONFIRMED, else None.
    The decision is stored for the board. Never raises."""
    try:
        d = _load()
        res = decide(market, now_ts, d)
        d.setdefault("decision", {})[market] = {k: res[k] for k in ("decision", "score", "modules", "reasons", "side")} | {"ts": int(now_ts)}
        alert = None
        if res["decision"].startswith("CONFIRMED") and res["id"] not in d["sent"]:
            d["sent"][res["id"]] = int(now_ts)
            alert = {"text": format_alert(res, digits), "side": res["side"], "id": res["id"], "market": market}
        d["sent"] = {k: t for k, t in d["sent"].items() if now_ts - t < 3 * 86400}
        _save(d)
        return alert
    except Exception:                                              # noqa: BLE001  (never stops the bot)
        return None


def board(now_ts=None):
    """Compliance board: every module, the unified decision, the score and the number of active confirmations."""
    now_ts = int(now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp())
    d = _load()
    markets = sorted(set(d["votes"]) | set(d["main"]) | set(d.get("decision", {})))
    if not markets:
        return "UNIFIED BOARD: no module has voted yet."
    lines = ["UNIFIED BOARD (information only)"]
    active = 0
    for m in markets:
        r = decide(m, now_ts, d)
        main = r["main"]["side"].upper() if r["main"] else "WAIT"
        lines += [f"{m}:", f"  SMC Prime: {r['modules']['smc']}", f"  Order Flow Pulse: {r['modules']['pulse']} (estimate)",
                  f"  ICT Session Scalper: {r['modules']['scalper']}", f"  Main SELECTIVE chain: {main}",
                  f"  Unified Decision: {r['decision']}   Confluence Score: {r['score']:.0f}/100"]
        for why in r["reasons"]:
            lines.append(f"  - {why}")
        if r["decision"].startswith("CONFIRMED"):
            active += 1
    lines.append(f"Active confirmed signals: {active}")
    return "\n".join(lines)
