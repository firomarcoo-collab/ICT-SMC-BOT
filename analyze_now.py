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

import extras  # noqa: E402
import ict_alerts  # noqa: E402
import signal_store  # noqa: E402


from report_tools import TF_SECS, directions, fib_format, fib_text  # noqa: E402,F401


def analyze(name, env=None):
    env = os.environ if env is None else env
    os.environ["VERBOSE"] = "1"        # full report for this one analysis only
    try:
        return _analyze(name, env)
    finally:
        os.environ.pop("VERBOSE", None)


def _analyze(name, env):
    now = int(time.time())
    scfgs = ict_alerts.build_selective_configs(env)
    # the live bot only accepts a confirmation candle closed within the last 3 minutes; an on-demand
    # analysis looks at the last closed M5 candle whenever you run it, so that freshness gate is lifted
    scfgs = [dataclasses.replace(c, max_signal_age=10 ** 9) for c in scfgs]
    store = signal_store.SignalStore(None)   # in memory: nothing is saved, nothing is locked
    status, alert, _ = ict_alerts.selective_scan(name, now, scfgs, store, ict_alerts.load_offsets(env, [name]), lang="ar")
    dirs = directions(name, now)
    lines = [f"== {name} | profile {'+'.join(c.profile.upper() for c in scfgs)} | score gate "
             f"{'/'.join(str(c.ready_score) for c in scfgs)}+ ==",
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
