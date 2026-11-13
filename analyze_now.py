"""One-shot full analysis of a market RIGHT NOW (prints only; no sound, no state saved, no lock taken).

Usage:  python analyze_now.py XAUUSD        (also BTCUSD, US30, US100)
        PROFILE=scalp python analyze_now.py BTCUSD

It runs the same selective engine as the live bot with the full report: H4/H1 trend, support /
resistance, liquidity sweep, MSS/BOS, FVG / order block, retest, closed M5 confirmation, costs,
the score out of 100, plus the trading session and news. It is analysis, not a guarantee.
"""
import dataclasses
import os
import sys
import time

os.environ["VERBOSE"] = "1"

import extras  # noqa: E402
import ict_alerts  # noqa: E402
import signal_store  # noqa: E402


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


def analyze(name, env=None):
    env = os.environ if env is None else env
    now = int(time.time())
    scfg = ict_alerts.build_selective_config(env)
    # the live bot only accepts a confirmation candle closed within the last 3 minutes; an on-demand
    # analysis looks at the last closed M5 candle whenever you run it, so that freshness gate is lifted
    scfg = dataclasses.replace(scfg, max_signal_age=10 ** 9)
    store = signal_store.SignalStore(None)   # in memory: nothing is saved, nothing is locked
    status, alert, _ = ict_alerts.selective_scan(name, now, scfg, store, lang="ar")
    dirs = directions(name, now)
    lines = [f"== {name} | profile {scfg.profile.upper()} | score gate {scfg.ready_score}+ ==",
             extras.session_text(now).strip(), dirs,
             fib_text(name, now, ict_alerts.INSTRUMENTS.get(name, {}).get('digits', 2)), status]
    if alert:
        lines += ["", "ENTRY SETUP FOUND on the last closed M5 candle (signal only, you place the trade yourself;",
                  "check the price is still near the entry, the setup may be a few minutes old):", alert["text"]]
    else:
        lines += ["", "No entry now. The reasons above say what is missing; wait for the next closed M5 candle."]
    return "\n".join(l for l in lines if l is not None)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Use: python analyze_now.py XAUUSD|BTCUSD|US30|US100")
    print(analyze(sys.argv[1].upper()))
