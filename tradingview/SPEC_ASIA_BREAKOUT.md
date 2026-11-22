# Spec: Gold Asia-range breakout (copied from `asia_breakout.py`)

Market: XAUUSD. Chart timeframe: M15, closed candles only. All times are America/New_York (daylight saving handled by the timezone, not by fixed UTC hours).

## Range
- Asia range = high / low from 19:00 (previous day) to 03:00 ET.
- Valid only if the width is between 1.0 and 3.0 x ATR(14) of the H1 timeframe.

## Breakout
- The FIRST M15 candle closing outside the range, closing between 03:00 and 08:00 ET.
- It qualifies only if all hold:
  - the close is beyond the edge by at least 0.1 x ATR(14, M15);
  - the body is at least 50% of the candle range;
  - tick volume is at least 1.2 x the average of the previous 20 candles (skipped when the feed has no volume);
  - the H1 structure trend agrees (up for a buy, down for a sell). In Pine use a documented proxy (for example H1 close versus EMA(50)); the bot uses its own structure trend, so this is an approximation.
- No new setups after 09:30 ET. A setup expires after 24 M15 candles or at 09:30 ET.

## After the breakout
- **Fake breakout:** two consecutive closes back inside the range cancel the setup.
- **Missed:** price more than 1.0 x ATR beyond the breakout candle's close without having retested the edge: do not chase.
- **Retest:** a candle whose wick comes back to the edge (within 0.1 x ATR).
- **Confirmation:** a closed M15 candle in the trade direction that closes beyond the edge, after the retest.

## Plan (on the confirmation candle)
- Entry: the close of the confirmation candle.
- Hard SL: the retest extreme (lowest low for a buy, highest high for a sell) -/+ 0.5 x ATR. Never at the range edge.
- Early Exit: an M15 close back inside the range, at the edge price.
- TP1 = 1R (then move the stop to break-even), TP2 = 2R (the tracked target), TP3 = 3R or the next liquidity at 2R or more.
- Risk per trade 1% or less; the daily loss limit and the news windows (30 minutes around CPI / NFP / FOMC) are applied by the bot and are NOT modelled in Pine unless a later stage adds them.

## Known differences from the bot
- No DXY filter in the bot; Pine may add one only as an optional input, default off.
- Spread / commission / cost check: the bot rejects signals whose cost is too high; the Pine strategy must set commission and slippage inputs explicitly.
- Everything above is a starting hypothesis, not a measured result.
