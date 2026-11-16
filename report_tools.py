"""Helpers shared by analyze_now.py and the bot's periodic market report (no side effects on import)."""
import ict_alerts


TF_SECS = {"4h": 14400, "1h": 3600, "15m": 900, "5m": 300, "1m": 60}


def directions(name, now):
    """Structure direction on H4, H1, M15, M5 and M1 (M1 is timing only: it never decides the direction)."""
    import structure as st
    inst = ict_alerts.INSTRUMENTS.get(name)
    if inst is None:
        return ""
    got = {}
    for tf, k in (("4h", 3), ("1h", 3), ("15m", 2), ("5m", 2), ("1m", 2)):
        try:
            candles = ict_alerts.get_candles(inst, tf)
            closed = [c for c in candles if int(c["time"].timestamp()) + TF_SECS[tf] <= now]
            tr = st.current_trend(st.structure_events(closed, k))
            got[tf] = "UP" if tr == "up" else "DOWN" if tr == "down" else "?"
        except Exception:
            got[tf] = "n/a"
    m15, m5, m1 = got["15m"], got["5m"], got["1m"]
    if m5 == m1 and m5 in ("UP", "DOWN"):
        verdict = f"M5 and M1 agree {m5}: look for a {'buy' if m5 == 'UP' else 'sell'} (scalp)"
        if all(h in ("UP", "DOWN") and h != m5 for h in (got["4h"], got["1h"])):
            verdict += "; H4 and H1 are both against it = blocked, even for a scalp"
        elif m15 in ("UP", "DOWN") and m15 != m5:
            verdict += "; M15 is against it (higher risk, a quick target only)"
    else:
        verdict = "M5 and M1 disagree or unknown: no scalp direction, wait"
    return ("Direction: " + " | ".join(f"{tf} {v}" for tf, v in got.items()) + f"  ->  {verdict}")


def fib_text(name, now, digits=2):
    """Fibonacci of the latest M15 leg: retracement levels, the 62-79% OTE zone, extension targets and
    where the price is now. Informational only: it never creates or blocks a signal."""
    import structure as st
    inst = ict_alerts.INSTRUMENTS.get(name)
    if inst is None:
        return ""
    try:
        candles = ict_alerts.get_candles(inst, "15m")
        closed = [c for c in candles if int(c["time"].timestamp()) + 900 <= now]
        pd = st.premium_discount(closed, 3)
    except Exception:
        return "Fibonacci (M15): data unavailable"
    return fib_format(pd, digits) if pd else "Fibonacci (M15): no clear swing leg yet"


def fib_format(pd, digits=2):
    f = lambda v: f"{v:.{digits}f}"  # noqa: E731
    leg = "up leg (pullback = buy zone)" if pd["direction"] == "up" else "down leg (pullback = sell zone)"
    ret = "  ".join(f"{int(r * 1000) / 10:g}%={f(v)}" for r, v in pd["fib"]["retracement"].items())
    ext = "  ".join(f"{e:g}={f(v)}" for e, v in pd["fib"]["extension"].items())
    price = pd.get("price")
    lo, hi = pd["ote"]
    where = ""
    if price is not None:
        where = ("price is INSIDE the OTE zone" if lo <= price <= hi else
                 "price is above the OTE zone" if price > hi else "price is below the OTE zone")
    return (f"Fibonacci (M15 leg {f(pd['low'])}-{f(pd['high'])}, {leg}): retracement {ret}\n"
            f"  OTE zone (62-79%): {f(lo)}-{f(hi)}  |  now {pd['zone']}, {where}\n"
            f"  extension targets: {ext}")


ARROW = {"UP": "UP", "DOWN": "DOWN", "?": "?", "n/a": "n/a"}


def direction_map(name, now):
    """{timeframe: 'UP'|'DOWN'|'?'|'n/a'} structure direction for H4, H1, M15, M5, M1."""
    import structure as st
    inst = ict_alerts.INSTRUMENTS.get(name)
    got = {}
    for tf, k in (("4h", 3), ("1h", 3), ("15m", 2), ("5m", 2), ("1m", 2)):
        try:
            candles = ict_alerts.get_candles(inst, tf)
            closed = [c for c in candles if int(c["time"].timestamp()) + TF_SECS[tf] <= now]
            tr = st.current_trend(st.structure_events(closed, k))
            got[tf] = "UP" if tr == "up" else "DOWN" if tr == "down" else "?"
        except Exception:
            got[tf] = "n/a"
    return got


def market_report(names, now_ts, wait_stats=None, offsets=None, signals=0):
    """An organised block, one line per market: price, direction of every timeframe, what it waits for.
    Printed by the bot every REPORT_MINUTES (default 60) so the analysis is regular and easy to read."""
    import extras
    from datetime import datetime, timezone
    offsets = offsets or {}
    lines = [f"===== MARKET REPORT {datetime.fromtimestamp(now_ts, tz=timezone.utc):%H:%M UTC} | "
             f"{extras.session_text(now_ts).strip()} | signals this hour: {signals} ====="]
    lines.append(dollar_line(dollar_state(now_ts)))
    for name in names:
        inst = ict_alerts.INSTRUMENTS.get(name)
        if inst is None:
            continue
        try:
            price = max(ict_alerts.get_candles(inst, "5m"), key=lambda c: c["time"])["close"] + offsets.get(name, 0.0)
            ptxt = f"{price:.{inst['digits']}f}"
        except Exception:
            ptxt = "no data"
        d = direction_map(name, now_ts)
        dirs = " ".join(f"{tf.upper() if tf != '15m' else 'M15'}:{v}" for tf, v in d.items()).replace("4H", "H4").replace("1H", "H1").replace("5M", "M5").replace("1M", "M1")
        agree = ""
        if d["5m"] == d["1m"] and d["5m"] in ("UP", "DOWN"):
            agree = f" | M5+M1 {d['5m']}" + (" = scalp bias" if d["15m"] == d["5m"] else "")
        top = sorted((wait_stats or {}).get(name, {}).items(), key=lambda kv: -kv[1])[:1]
        waiting = f" | waits: {top[0][0]}" if top else ""
        lines.append(f"{name:7} {ptxt:>11} | {dirs}{agree}{waiting}")
    return "\n".join(lines)


# ===== US dollar monitor =====
# DXY (the US dollar index) from the free Yahoo feed. Gold, EURUSD and (loosely) Bitcoin usually move against
# the dollar. This is context for the reader, it never blocks or creates a signal by itself.
DOLLAR = {"source": "yahoo", "id": "DX-Y.NYB", "digits": 3}
USD_INVERSE = ("XAUUSD", "EURUSD", "BTCUSD")


def dollar_state(now_ts):
    """{'price', 'h1', 'm15', 'change'} of the dollar index (trend UP/DOWN/? on H1 and M15, % change over
    24 hourly candles), or None when the feed is unavailable."""
    import structure as st
    try:
        h1 = [c for c in ict_alerts.get_candles(DOLLAR, "1h") if int(c["time"].timestamp()) + 3600 <= now_ts]
        m15 = [c for c in ict_alerts.get_candles(DOLLAR, "15m") if int(c["time"].timestamp()) + 900 <= now_ts]
        if len(h1) < 30 or len(m15) < 20:
            return None
        trend = lambda bars, k: {"up": "UP", "down": "DOWN"}.get(st.current_trend(st.structure_events(bars, k)), "?")  # noqa: E731
        last = h1[-1]["close"]
        return {"price": last, "h1": trend(h1, 3), "m15": trend(m15, 2),
                "change": (last / h1[-25]["close"] - 1) * 100 if len(h1) >= 25 else 0.0}
    except Exception:
        return None


def dollar_line(usd):
    if not usd:
        return "USD index (DXY): no data"
    return (f"USD index (DXY) {usd['price']:.3f} ({usd['change']:+.2f}% over 24h) | H1 {usd['h1']} | "
            f"M15 {usd['m15']}")


def dollar_signal_line(market, side, usd):
    """One line for a signal: does the dollar trend support the trade? '' for markets it does not matter to."""
    if not usd or market not in USD_INVERSE:
        return ""
    want_usd = "DOWN" if side == "buy" else "UP"          # a buy of gold/EURUSD/BTC likes a falling dollar
    verdict = ("supports" if usd["h1"] == want_usd else "is against" if usd["h1"] in ("UP", "DOWN") else
               "gives no clear direction for")
    return (f"USD (DXY): H1 {usd['h1']}, M15 {usd['m15']}, {usd['change']:+.2f}% over 24h: the dollar "
            f"{verdict} this {side} (context only, the dollar often moves opposite to {market})")
