"""UNIFIED TRADE CONFIRMATION ENGINE (signals only: it never places an order).
The ONLY place that issues CONFIRMED BUY / CONFIRMED SELL. Three modules form a hierarchy, not a vote count:
  Level 1  SMC Prime (35)             Market Structure & Direction
  Level 2  ICT Session Scalper (35)   Liquidity & Entry Timing
  Level 3  Order Flow Pulse (30)      Momentum Confirmation (built from candle volume/range, NOT real order flow)
Decision order: any disagreement in direction = NO TRADE (whatever the score); the mandatory conditions (liquidity sweep, CHoCH/MSS,
displacement, FVG/OB, retest, closed M15 confirmation) can never be bought back with points; then Level 1, 2, 3 must each confirm;
only then the score (min 80) is computed. Only closed candles and fresh votes count; a confirmed signal is valid for a limited time and is
cancelled when its invalidation level closes against it or a module turns against it. Each module may answer WAIT.
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
VALID_MIN_DEFAULT = 90                                            # minutes a confirmed signal stays valid for NEW entries
TF_SEC = {"smc": 900, "scalper": 300, "pulse": 300, "main": 900}   # candle length behind each vote (all must be CLOSED)
PULSE_FRESH = 1800                                               # a Pulse older than 30 min earns no 'fresh' check
MIN_SCORE = 80
SIDE = {"long": "buy", "short": "sell", "buy": "buy", "sell": "sell"}


def enabled():
    """UNIFIED=0 restores the old behaviour (the main chain alerts by itself)."""
    return os.environ.get("UNIFIED", "1") != "0"


def valid_minutes():
    try:
        return float(os.environ.get("UNIFIED_VALID_MIN") or VALID_MIN_DEFAULT)
    except ValueError:
        return float(VALID_MIN_DEFAULT)


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
    d.setdefault("active", {})
    return d


def _save(d):
    try:
        with open(_file(), "w") as f:
            json.dump(d, f, default=str)
    except OSError:
        pass


def _bar_ts(bar_time):
    """Open time (UTC seconds) of a candle given as ISO text or epoch seconds; None when unreadable."""
    try:
        if isinstance(bar_time, (int, float)) or str(bar_time).isdigit():
            return int(bar_time)
        d = datetime.fromisoformat(str(bar_time))
        return int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp())
    except (ValueError, TypeError):
        return None


def candle_closed(bar_time, tf_sec, now_ts):
    """True only when the candle that opened at bar_time has already finished (an unfinished candle never counts)."""
    t = _bar_ts(bar_time)
    return t is not None and now_ts >= t + tf_sec


def on_event(market, ev, h1_trend, now_ts):
    """A module event from session_alerts. SIGNAL = a live vote; INVALIDATED / MISSED / REJECTED = the module answers WAIT.
    A vote built on a candle that is not closed yet is refused. Returns True when the event changed the state."""
    key = KEY.get(ev.get("name"))
    if not key:
        return False
    d = _load()
    mv = d["votes"].setdefault(market, {})
    side = SIDE.get(ev.get("direction"))
    kind = ev.get("event")
    if kind == "SIGNAL":
        if not candle_closed(ev.get("bar_time"), TF_SEC[key], now_ts):
            return False
        mv[key] = {"side": side, "ts": int(now_ts), "entry": ev["entry"], "sl": ev["sl"], "tp1": ev["tp1"], "tp2": ev["tp2"],
                   "rr": ev.get("rr"), "zone": ev.get("zone"), "proxy": bool(ev.get("proxy")), "h1": h1_trend,
                   "reason": ev.get("reason", ""), "bar_time": ev.get("bar_time")}
    elif kind in ("INVALIDATED", "MISSED", "REJECTED"):
        cur = mv.get(key)
        if cur and cur.get("side") not in (None, side):
            return False                                           # an event about the other direction does not erase this vote
        mv[key] = {"side": None, "ts": int(now_ts), "reason": f"{kind.lower()}: {ev.get('reason', '')}", "bar_time": ev.get("bar_time")}
    else:
        return False
    _save(d)
    return True


def record_main(market, setup, report, now_ts, sid, checks=None, decision_id=None):
    """The main SELECTIVE chain produced a signal. Refused when its M15 candle is not closed. The mandatory conditions come from the
    chain's own checks and are re-verified in decide()."""
    if not candle_closed(setup.get("bar_time"), TF_SEC["main"], now_ts):
        return False
    d = _load()
    d["main"][market] = {"side": setup["side"], "ts": int(now_ts), "id": str(sid or f"{market}-{setup.get('bar_time')}"),
                         "entry": setup["entry"], "sl": setup["sl"], "tp1": setup.get("tp1"), "tp2": setup["tp"],
                         "early_exit": setup.get("early_exit"), "score100": setup.get("score100"),
                         "trends": setup.get("trends") or {}, "report": report or {}, "checks": dict(checks or {}),
                         "parts": dict(setup.get("score_parts") or {}), "bar_time": setup.get("bar_time"),
                         "decision_id": decision_id, "setup": setup}
    _save(d)
    return True


def mandatory(main):
    """The six conditions that points can never replace. Returns the list of missing ones (empty = all present)."""
    c, parts = main.get("checks") or {}, main.get("parts") or {}
    need = [("liquidity sweep", c.get("liquidity")), ("CHoCH/MSS", c.get("bos_mss")),
            ("displacement", (parts.get("displacement") or 0) > 0), ("FVG/OB zone", c.get("fvg_ob")),
            ("retest of the zone", c.get("retest")), ("closed M15 confirmation candle", c.get("confirm"))]
    return [n for n, ok in need if not ok]


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


LEVELS = (("smc", "Level 1 - Market Structure & Direction (SMC Prime)"),
          ("scalper", "Level 2 - Liquidity & Entry Timing (ICT Session Scalper)"),
          ("pulse", "Level 3 - Momentum Confirmation (Order Flow Pulse, candle-based estimate)"))


def decide(market, now_ts=None, d=None):
    """Pure decision from the stored state. Returns decision, score, modules, levels, reasons, main, id."""
    now_ts = int(now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp())
    d = d or _load()
    votes, main = _live(d, market, now_ts)
    live = {k: v for k, v in votes.items() if v.get("side")}
    mods = {k: (live[k]["side"].upper() if k in live else "WAIT") for k in WEIGHTS}
    out = {"market": market, "modules": mods, "main": main, "score": 0.0, "decision": "WAIT", "side": None, "reasons": [],
           "id": None, "levels": {}}
    sides = {v["side"] for v in live.values()} | ({main["side"]} if main else set())
    if len(sides) > 1:                                             # direction conflict: NO TRADE whatever the score
        out["decision"] = "NO TRADE"
        who = ", ".join(f"{LABEL[k]} {v['side'].upper()}" for k, v in live.items())
        out["reasons"].append("conflict: " + who + (f", main chain {main['side'].upper()}" if main else ""))
        return out
    if not main:
        out["reasons"].append("no live signal from the main SELECTIVE chain (sweep, CHoCH/MSS, displacement, FVG/OB retest, M15 confirmation)")
        out["side"] = next(iter(sides)) if sides else None
        return out
    side = main["side"]
    out["side"] = side
    lost = mandatory(main)
    if lost:                                                       # never compensated by points
        out["reasons"].append("mandatory condition missing: " + ", ".join(lost))
        return out
    stop = False
    for key, title in LEVELS:
        ok = key in live
        out["levels"][key] = "CONFIRMED" if ok else "WAIT"
        if not ok and not stop:
            why = (votes.get(key) or {}).get("reason")
            out["reasons"].append(f"{title}: not confirmed" + (f" ({why})" if why else ""))
            stop = True
    if stop:
        return out
    score = 0.0
    for k, v in live.items():
        score += WEIGHTS[k] * sum(_checks(k, v, side, now_ts)) / 3
    out["score"] = round(score, 1)
    if score < MIN_SCORE:
        out["reasons"].append(f"score {score:.0f}/100 is below the minimum {MIN_SCORE}")
        return out
    out["decision"] = "CONFIRMED " + side.upper()
    out["id"] = f"{market}-UNI-{side}-{main['id']}"
    out["votes"] = live
    return out


def _n(v, digits):
    return "-" if v is None else f"{v:.{digits}f}"


def _utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M UTC")


def pulse_note(votes):
    p = (votes or {}).get("pulse") or {}
    return ("CONFIRMED (momentum from candle range: this feed has no volume; not real order flow)" if p.get("proxy")
            else "CONFIRMED (momentum from candle volume/range data, not real order flow)")


def format_alert(res, digits=2, now_ts=None):
    m, votes = res["main"], res["votes"]
    zs = [m["entry"]] + [v["entry"] for v in votes.values()]
    rep = m.get("report") or {}
    tr = m.get("trends") or {}
    side = res["side"]
    bias = "BULLISH" if side == "buy" else "BEARISH"
    risk = abs(m["entry"] - m["sl"])
    rr = abs(m["tp2"] - m["entry"]) / risk if risk else 0.0
    now_ts = int(now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp())
    until = _utc(now_ts + valid_minutes() * 60)
    ee = m.get("early_exit")
    return "\n".join([
        f"KENAN TRADER AI - {res['decision']}", "",
        f"Symbol: {res['market']}", f"Direction: {side.upper()}",
        f"Entry Zone: {_n(min(zs), digits)} - {_n(max(zs), digits)}",
        f"Stop Loss: {_n(m['sl'], digits)}",
        f"Take Profit 1: {_n(m.get('tp1'), digits)} (1R: then move the stop to break-even by hand)",
        f"Take Profit 2: {_n(m['tp2'], digits)}", "",
        "SMC Prime (structure & direction): CONFIRMED",
        "ICT Session Scalper (liquidity & timing): CONFIRMED",
        f"Order Flow Pulse (momentum): {pulse_note(votes)}", "",
        f"Confluence Score: {res['score']:.0f}/100 (counts conditions, not a win probability)",
        "Timeframe: M15 entry (H4/H1 context)", f"Market Bias: {bias}",
        f"Higher timeframes: H4 {tr.get('h4', '-')} | H1 {tr.get('h1', '-')}",
        f"Liquidity: {rep.get('liquidity') or '-'}", f"Structure: {rep.get('structure') or '-'}",
        f"Displacement: {rep.get('reversal') or '-'}", f"FVG/OB Retest: {rep.get('retest') or '-'}",
        f"Confirmation: {rep.get('confirm') or '-'}", "",
        f"Risk/Reward to TP2: 1:{rr:.1f} | Risk per trade: {os.environ.get('RISK_PERCENT') or '0.5'}% of the account",
        f"Valid until: {until} ({valid_minutes():.0f} min). After that do not enter.",
        f"Cancelled if: a full M15 candle closes beyond {_n(ee, digits)}, or a module turns against it.",
        "Status: VALID TRADE SIGNAL", "Signal only: place the trade yourself in MT5. No win rate is guaranteed."])


def run_market(market, now_ts, digits=2, fresh=True):
    """Called after every scan of a market. Returns an alert dict ONCE per main signal when the decision is CONFIRMED, else None.
    fresh=False (the data feed is lagging) never confirms. The decision is stored for the board. Never raises."""
    try:
        d = _load()
        res = decide(market, now_ts, d)
        d.setdefault("decision", {})[market] = {k: res[k] for k in ("decision", "score", "modules", "reasons", "side")} | {"ts": int(now_ts)}
        alert = None
        if res["decision"].startswith("CONFIRMED") and res["id"] not in d["sent"]:
            if not fresh:
                d["decision"][market]["decision"] = "WAIT"
                d["decision"][market]["reasons"] = ["the data feed is not fresh: no confirmation on old candles"]
            else:
                m = res["main"]
                d["sent"][res["id"]] = int(now_ts)
                d["active"][res["id"]] = {"market": market, "side": res["side"], "ts": int(now_ts),
                                          "expires": int(now_ts + valid_minutes() * 60), "early_exit": m.get("early_exit"),
                                          "sl": m["sl"], "status": "ACTIVE"}
                alert = {"text": format_alert(res, digits, now_ts), "side": res["side"], "id": res["id"], "market": market,
                         "setup": m.get("setup"), "bar_time": m.get("bar_time"), "main_id": m["id"], "decision_id": m.get("decision_id")}
        d["sent"] = {k: t for k, t in d["sent"].items() if now_ts - t < 3 * 86400}
        d["active"] = {k: a for k, a in d["active"].items() if now_ts - a["ts"] < 3 * 86400}
        _save(d)
        return alert
    except Exception:                                              # noqa: BLE001  (never stops the bot)
        return None


def check_active(market, last_m15, now_ts):
    """Cancels / expires this market's confirmed signals. last_m15 = the last CLOSED M15 candle (dict with close) or None.
    Returns [(status, text)]: CANCELLED (invalidation closed against it, or a module turned against it) or EXPIRED (time is up)."""
    out = []
    try:
        d = _load()
        res = decide(market, now_ts, d)
        for sid, a in list(d["active"].items()):
            if a["market"] != market or a["status"] != "ACTIVE":
                continue
            why = None
            if last_m15 is not None and a.get("early_exit") is not None:
                c = last_m15["close"]
                if (a["side"] == "buy" and c < a["early_exit"]) or (a["side"] == "sell" and c > a["early_exit"]):
                    why = f"a full M15 candle closed at {c:g}, beyond the invalidation level {a['early_exit']:g}"
            if why is None and res["decision"] == "NO TRADE":
                why = "a module turned against it: " + "; ".join(res["reasons"])
            if why:
                a["status"] = "CANCELLED"
                out.append(("CANCELLED", f"KENAN TRADER AI - SIGNAL CANCELLED\n{market} {a['side'].upper()}: {why}.\n"
                                         "If you entered, manage or close it yourself. Signal only."))
            elif now_ts >= a["expires"]:
                a["status"] = "EXPIRED"
                out.append(("EXPIRED", f"KENAN TRADER AI - SIGNAL EXPIRED\n{market} {a['side'].upper()}: its validity ended at "
                                       f"{_utc(a['expires'])}. Do not enter any more."))
        if out:
            _save(d)
    except Exception:                                              # noqa: BLE001
        pass
    return out


def board(now_ts=None):
    """Compliance board: every module, the unified decision, the score and the active confirmed signals."""
    now_ts = int(now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp())
    d = _load()
    markets = sorted(set(d["votes"]) | set(d["main"]) | set(d.get("decision", {})))
    if not markets:
        return "UNIFIED BOARD: no module has voted yet."
    lines = ["UNIFIED BOARD (information only, signals only)"]
    for m in markets:
        r = decide(m, now_ts, d)
        main = r["main"]["side"].upper() if r["main"] else "WAIT"
        lines += [f"{m}:", f"  SMC Prime: {r['modules']['smc']}", f"  Order Flow Pulse: {r['modules']['pulse']} (candle-based estimate)",
                  f"  ICT Session Scalper: {r['modules']['scalper']}", f"  Main SELECTIVE chain: {main}",
                  f"  Unified Decision: {r['decision']}   Confluence Score: {r['score']:.0f}/100"]
        for why in r["reasons"]:
            lines.append(f"  - {why}")
    act = [(k, a) for k, a in d["active"].items() if a["status"] == "ACTIVE" and now_ts < a["expires"]]
    lines.append(f"Active confirmed signals: {len(act)}")
    for k, a in act:
        conflict = decide(a["market"], now_ts, d)["decision"] == "NO TRADE"
        lines.append(f"  - {a['market']} {a['side'].upper()} valid until {_utc(a['expires'])}"
                     + (" (a module turned against it: it is cancelled on the next scan)" if conflict else ""))
    return "\n".join(lines)
