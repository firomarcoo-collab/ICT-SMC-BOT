# TradingView (Pine Script v6)

Separate from the Python bot: nothing in Python imports from this folder, `requirements.txt` is untouched, and `pytest` does not collect `.pine` files. Pine scripts are pasted into the TradingView editor; they never run in Termux and never trade.

Rules (same as the bot): signals only, closed candles only, no profit or win-rate promises, no secrets in these files.

## Plan (stages)
1. **Spec** (this stage): `SPEC_ASIA_BREAKOUT.md`, the exact rules and numbers copied from `asia_breakout.py`. No code yet.
2. `asia_breakout_indicator.pine`: an `indicator()` with the Asia range, the breakout / retest / confirmation marks and `alert()` calls (one alert on the indicator, condition "Any alert() function call"). Written but NOT compiled in TradingView yet: send any editor error back to fix it.
3. `asia_breakout_strategy.pine`: a `strategy()` for historical testing with spread and commission.
4. Parity check against the Python bot on the same candles. Small differences are expected (tick volume of a CFD, multi-timeframe handling), so Pine results are a second opinion, not a guarantee.

## Before using a script
- The editor must accept `//@version=6`; check it in your own account.
- Use the same symbol and broker feed you trade (an index or gold CFD, not another venue), and chart timeframe M15.
- A backtest in TradingView is not a promise of future results.
