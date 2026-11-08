"""RCT: Reversal and Continuation signals on the last closed candle.

Same candle layout as the other modules: newest first, r[0] is the forming bar.
Entry is the close of the signal candle (place it as a limit order at that price).

CONTINUATION: clear trend (price > EMA50 > EMA200 or the opposite), a recent pullback to
the EMA50, then a candle that resumes the trend (closes beyond the previous candle).
REVERSAL (counter-trend, higher risk): RSI stretched, price takes out the recent extreme
(liquidity sweep) and a rejection candle (hammer / shooting star / engulfing) closes back.
"""
from classic import bearish_engulfing, bullish_engulfing, ema, hammer, shooting_star, trend
from ict_smc_bot import get_atr

PULLBACK_BARS = 6
EMA_TOUCH_ATR = 0.25
RSI_PERIOD = 14
RSI_LOW, RSI_HIGH = 30, 70
EXTREME_BARS = 20
MIN_RISK_ATR, MAX_RISK_ATR = 0.3, 4.0


def rsi(closes, period=RSI_PERIOD):
    """Wilder RSI of the last value; None if there is not enough data."""
    if len(closes) <= period:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_gain, avg_loss = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _make(side, mode, entry, sl, atr, cfg):
    risk = abs(entry - sl)
    if not (MIN_RISK_ATR * atr <= risk <= MAX_RISK_ATR * atr):
        return None
    tp = entry + risk * cfg.rr if side == "buy" else entry - risk * cfg.rr
    return {"side": side, "entry": entry, "sl": sl, "tp": tp, "mode": mode}


def find_rct(r, cfg):
    """Returns {'side','entry','sl','tp','mode'} (mode: continuation or reversal) or None."""
    need = cfg.ema_slow + EXTREME_BARS + 5
    if len(r) < need:
        return None
    atr = get_atr(r, cfg.atr_period)
    if atr <= 0:
        return None
    closes = [c["close"] for c in reversed(r[1:])]
    c1, c2 = r[1], r[2]
    buf = atr * cfg.sl_buffer_atr

    # ---- continuation ----
    tr = trend(r, cfg)
    if tr in ("up", "down"):
        fast = ema(closes, cfg.ema_fast)
        recent = r[1:1 + PULLBACK_BARS]
        if tr == "up":
            touched = min(c["low"] for c in recent) <= fast + atr * EMA_TOUCH_ATR
            resume = c1["close"] > c1["open"] and c1["close"] > c2["high"] and c1["close"] > fast
            if touched and resume:
                s = _make("buy", "continuation", c1["close"], min(c["low"] for c in recent) - buf, atr, cfg)
                if s:
                    return s
        else:
            touched = max(c["high"] for c in recent) >= fast - atr * EMA_TOUCH_ATR
            resume = c1["close"] < c1["open"] and c1["close"] < c2["low"] and c1["close"] < fast
            if touched and resume:
                s = _make("sell", "continuation", c1["close"], max(c["high"] for c in recent) + buf, atr, cfg)
                if s:
                    return s

    # ---- reversal ----
    before = closes[:-1]  # closes up to the candle before the signal candle
    value = rsi(before)
    if value is None:
        return None
    prior = r[2:2 + EXTREME_BARS]
    if (value < RSI_LOW and c1["low"] < min(c["low"] for c in prior)
            and c1["close"] > c1["low"] and (hammer(c1) or bullish_engulfing(c2, c1))):
        return _make("buy", "reversal", c1["close"], c1["low"] - buf, atr, cfg)
    if (value > RSI_HIGH and c1["high"] > max(c["high"] for c in prior)
            and (shooting_star(c1) or bearish_engulfing(c2, c1))):
        return _make("sell", "reversal", c1["close"], c1["high"] + buf, atr, cfg)
    return None
