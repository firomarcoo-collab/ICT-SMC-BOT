"""Replay the signals on past candles and count what would have happened.

Usage:  python backtest.py BTCUSD 30
        (symbol, days of history; Yahoo symbols are limited to the last 60 days)

How the simulation works:
- one trade at a time, like the EA: no new signal while an order or trade is open
- a signal places a limit order; it fills when price touches the entry
  (an order that does not fill within MAX_WAIT bars is cancelled)
- on the fill bar only the stop loss is checked, later bars check the stop first,
  then the target (the pessimistic choice when one bar touches both)
- result is in R: a loss is -1, a win is the reward-to-risk of the trade
- NO spread, commission or slippage, so real results are worse than these numbers
"""
import sys
import time
import urllib.parse

from classic import find_classic_setup, ict_confluence
from rct import find_rct
from ict_alerts import INSTRUMENTS, fetch_json, parse_binance, parse_yahoo
from ict_smc_bot import Config, find_setup

MAX_WAIT = 24  # bars (2 hours on M5)


def get_history(name, days):
    inst = INSTRUMENTS[name]
    if inst["source"] == "binance":
        out = []
        start = int((time.time() - days * 86400) * 1000)
        while True:
            rows = None
            for host in ("https://api.binance.com", "https://data-api.binance.vision"):
                try:
                    rows = fetch_json(f"{host}/api/v3/klines?symbol={inst['id']}"
                                      f"&interval=5m&limit=1000&startTime={start}")
                    break
                except Exception:
                    continue
            if not rows:
                break
            out += parse_binance(rows)
            start = rows[-1][0] + 300_000
            if len(rows) < 1000:
                break
        return out
    sym = urllib.parse.quote(inst["id"], safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=5m&range=60d"
    return parse_yahoo(fetch_json(url))


def _r_multiple(order, win):
    if not win:
        return -1.0
    risk = abs(order["entry"] - order["sl"])
    return abs(order["tp"] - order["entry"]) / risk


def _exit(order, c, check_target=True):
    """True = target hit, False = stop hit, None = still open."""
    if order["side"] == "buy":
        if c["low"] <= order["sl"]:
            return False
        if check_target and c["high"] >= order["tp"]:
            return True
    else:
        if c["high"] >= order["sl"]:
            return False
        if check_target and c["low"] <= order["tp"]:
            return True
    return None


def simulate(candles, cfg, kind="ict", digits=2, warmup=250, window=300, max_wait=MAX_WAIT):
    """candles: oldest first. kind: 'ict' or 'classic'. Returns a list of trades."""
    trades = []
    pending = None
    position = None
    for i, c in enumerate(candles):
        if position is not None:
            result = _exit(position, c)
            if result is not None:
                trades.append({"R": _r_multiple(position, result), "score": position["score"],
                               "side": position["side"], "time": c["time"]})
                position = None
        elif pending is not None:
            touched = (c["low"] <= pending["entry"] if pending["side"] == "buy"
                       else c["high"] >= pending["entry"])
            if touched:
                position, pending = pending, None
                result = _exit(position, c, check_target=False)  # fill bar: stop only
                if result is not None:
                    trades.append({"R": -1.0, "score": position["score"],
                                   "side": position["side"], "time": c["time"]})
                    position = None
            elif i - pending["placed"] >= max_wait:
                pending = None

        if position is None and pending is None and i >= warmup:
            r = list(reversed(candles[max(0, i - window + 1): i + 1]))
            price = r[0]["close"]
            if kind == "ict":
                setup = find_setup(r, cfg, ask=price, bid=price, digits=digits)
                score = len(ict_confluence(r, cfg, setup)) if setup else None
            else:
                setup = find_rct(r, cfg) if kind == "rct" else find_classic_setup(r, cfg)
                if setup:
                    setup = {k: (round(v, digits) if isinstance(v, float) else v)
                             for k, v in setup.items()}
                score = None
            if setup:
                pending = dict(setup, score=score, placed=i)
    return trades


def summarize(label, trades):
    if not trades:
        return f"{label}: no trades"
    wins = sum(1 for t in trades if t["R"] > 0)
    total = sum(t["R"] for t in trades)
    return (f"{label}: {len(trades)} trades, {wins} wins ({100 * wins / len(trades):.0f}%), "
            f"total {total:+.1f}R, average {total / len(trades):+.2f}R")


def report(name, days, candles, cfg, digits):
    print(f"{name}: {len(candles)} candles ({days} days requested)")
    ict = simulate(candles, cfg, "ict", digits)
    print(summarize("ICT, all signals", ict))
    for score in range(5):
        subset = [t for t in ict if t["score"] == score]
        if subset:
            print("  " + summarize(f"confirmations = {score}", subset))
    for minimum in (1, 2, 3):
        subset = [t for t in ict if t["score"] >= minimum]
        if subset:
            print("  " + summarize(f"confirmations >= {minimum}", subset))
    print(summarize("CLASSIC", simulate(candles, cfg, "classic", digits)))
    rct_trades = simulate(candles, cfg, "rct", digits)
    print(summarize("RCT", rct_trades))


def main():
    name = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSD").upper()
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    if name not in INSTRUMENTS:
        raise SystemExit(f"Unknown symbol {name}. Choose from {list(INSTRUMENTS)}")
    candles = sorted(get_history(name, days), key=lambda c: c["time"].timestamp())
    if len(candles) < 400:
        raise SystemExit(f"Only {len(candles)} candles came back, not enough to test")
    report(name, days, candles, Config(), INSTRUMENTS[name]["digits"])
    print("\nNo spread, commission or slippage included. Fewer than ~30 trades "
          "says very little. Past results do not predict future results.")


if __name__ == "__main__":
    main()
