# PROJECT_STATUS

Last update: 2026-11-11 | Branch: `phase2-restart-persistence` (not merged into `main`)

## Done (this branch)
- Restart: saved signals are followed again (`restore_tracking`); order-dependent cache test fixed.
- Score out of 100 (READY 86+, WATCH 75-85, REJECT); risk defaults 0.25% / 3 losses / 1.5% a day.
- Final states TP_HIT / SL_HIT / CANCELLED / EXPIRED (old names still accepted).
- Profiles: strict (86+), active (80+), loose (60+, body-share strong confirmation).
- Fix: an unset READY_SCORE no longer overrides the profile threshold in the live bot.
- Backtest runner (`backtest_runner.py`) + downloaders: Binance (`fetch_binance_history.py`),
  Dukascopy gold (`fetch_dukascopy_history.py`, unstable from the user's network).
- Startup line now prints profile, score gate and risk limits.

## Tests
- Quick suite: 187 passed. Full suite incl. slow backtest tests: 201 passed on the previous commit
  (this commit changed only `ict_alerts.py` startup text + one test; slow tests not re-run).

## Measured so far
- BTCUSDT, 180 days, active profile, spread 20 assumed: 17 trades, 65% wins, PF 3.34, +15.1R.
  Far below the 200-trade minimum: NOT evidence. Gold / indices not measured.

## Open issues
- No MT5 connection: prices come from free feeds (gold = Binance PAXG, offset +8 unverified).
- Spreads unknown for BTCUSD / US30; bot runs with ALLOW_UNKNOWN_COST=1 (signals marked COST UNVERIFIED).
- Dukascopy download for gold unfinished (503 / connection resets).
- `markets.json` specs incomplete (tick size/value, commission).
- Stop-hook warns about unpushed commits although the remote branch is up to date (false alarm).

## Next step
- Get the MT5 gold Bid at the same moment as the bot's feed price to compute `OFFSET_XAUUSD`,
  then finish the gold history download and backtest it.
