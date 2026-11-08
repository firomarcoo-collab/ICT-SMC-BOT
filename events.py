"""Early heads-up events on the last closed candle: liquidity sweep, FVG, order block.
r is newest first; r[0] is the forming bar, r[1] the last closed bar (same as find_setup)."""
from ict_smc_bot import get_atr

FVG_MIN_ATR = 0.5  # ignore tiny gaps


def find_events(r, cfg, digits=2, kinds=("sweep", "fvg", "ob")):
    """Returns a list of {'kind', 'side', 'text'}. side = the direction it hints at."""
    need = cfg.lookback + cfg.atr_period + 5
    if len(r) < need:
        return []
    atr = get_atr(r, cfg.atr_period)
    if atr <= 0:
        return []
    f = lambda v: f"{v:.{digits}f}"
    out = []

    if "sweep" in kinds:
        window = r[2:cfg.lookback + 2]
        old_low = min(c["low"] for c in window)
        old_high = max(c["high"] for c in window)
        if r[1]["low"] < old_low and r[1]["close"] > old_low:
            out.append({"kind": "sweep", "side": "buy", "text":
                        f"LIQUIDITY SWEEP: lows below {f(old_low)} were taken and price closed back above "
                        f"(wick low {f(r[1]['low'])}). Watch for a BUY if a bullish push and FVG follow."})
        if r[1]["high"] > old_high and r[1]["close"] < old_high:
            out.append({"kind": "sweep", "side": "sell", "text":
                        f"LIQUIDITY SWEEP: highs above {f(old_high)} were taken and price closed back below "
                        f"(wick high {f(r[1]['high'])}). Watch for a SELL if a bearish push and FVG follow."})

    if "fvg" in kinds:
        if r[1]["low"] - r[3]["high"] >= atr * FVG_MIN_ATR:
            out.append({"kind": "fvg", "side": "buy", "text":
                        f"BULLISH FVG: gap zone {f(r[3]['high'])} - {f(r[1]['low'])}. "
                        f"A pullback into it is a possible BUY area."})
        if r[3]["low"] - r[1]["high"] >= atr * FVG_MIN_ATR:
            out.append({"kind": "fvg", "side": "sell", "text":
                        f"BEARISH FVG: gap zone {f(r[1]['high'])} - {f(r[3]['low'])}. "
                        f"A pullback into it is a possible SELL area."})

    if "ob" in kinds:
        body = abs(r[1]["close"] - r[1]["open"])
        if (r[1]["close"] > r[1]["open"] and r[1]["close"] > r[2]["high"]
                and body >= atr * cfg.displacement_atr and r[2]["close"] < r[2]["open"]):
            out.append({"kind": "ob", "side": "buy", "text":
                        f"BULLISH ORDER BLOCK: last down candle {f(r[2]['low'])} - {f(r[2]['high'])} "
                        f"before a strong push up. Possible BUY area on a return to it."})
        if (r[1]["close"] < r[1]["open"] and r[1]["close"] < r[2]["low"]
                and body >= atr * cfg.displacement_atr and r[2]["close"] > r[2]["open"]):
            out.append({"kind": "ob", "side": "sell", "text":
                        f"BEARISH ORDER BLOCK: last up candle {f(r[2]['low'])} - {f(r[2]['high'])} "
                        f"before a strong push down. Possible SELL area on a return to it."})
    return out
