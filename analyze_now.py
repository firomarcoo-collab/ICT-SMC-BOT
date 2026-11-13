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


def directions(name, now):
    """Structure direction on M15, M5 and M1 (M1 is timing only: it never decides the direction)."""
    import structure as st
    inst = ict_alerts.INSTRUMENTS.get(name)
    if inst is None:
        return ""
    parts = []
    for tf, k in (("15m", 2), ("5m", 2), ("1m", 2)):
        try:
            candles = ict_alerts.get_candles(inst, tf)
            closed = [c for c in candles if int(c["time"].timestamp()) + {"15m": 900, "5m": 300, "1m": 60}[tf] <= now]
            tr = st.current_trend(st.structure_events(closed, k))
            parts.append(f"{tf} {'UP' if tr == 'up' else 'DOWN' if tr == 'down' else '?'}")
        except Exception:
            parts.append(f"{tf} n/a")
    m15, m5, m1 = (p.split()[1] for p in parts)
    if m15 == m5 and m15 in ("UP", "DOWN"):
        verdict = f"M15 and M5 agree {m15}: look for a {'buy' if m15 == 'UP' else 'sell'}" + (
            f"; M1 timing {'confirms' if m1 == m15 else 'does not confirm yet (wait)'}")
    else:
        verdict = "M15 and M5 disagree or unknown: no scalp direction, wait"
    return "Direction: " + " | ".join(parts) + f"  ->  {verdict}"


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
             extras.session_text(now).strip(), dirs, status]
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
