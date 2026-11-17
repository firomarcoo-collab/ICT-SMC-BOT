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
- Screen line `WATCH (score N/100 ...)` for a setup that passed every rule and only missed the score gate (print-only, no sound).

- AI review pack: every entry signal is copied to the clipboard (termux-clipboard-set) and saved to
  `last_signal_review.txt` with a review request; `REVIEW_COPY=0` disables. No external AI service is called.

## Tests
- Quick suite: 188 passed. Full suite incl. slow backtest tests: 201 passed on the previous commit
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


## Forex upgrade (request of 2026-11-15) - staged
Backup: git tag `backup-before-forex-upgrade` is local only (tag push gets HTTP 403); the branch `phase2-restart-persistence` on GitHub at 023a3ca is the restore point.
- Stage 1 (done): symbols EURUSD (already present), GBPJPY (new, in the default list), JPYGBP (new, opt-in); pip size helper `markets.pip_size`; markets.json entries (all unverified, nothing assumed); news currencies.
- Cannot be done on Android: reading symbols/Bid/Ask/specs/server time from MT5 (the MetaTrader5 Python package is Windows only; `MT5Provider` stays a stub). The broker symbol name, spread and contract spec must be copied by hand from MT5 into markets.json. Prices come from Yahoo feeds.
- Stage 2 (planned, new `forex` profile, existing profiles untouched): H4/H1 bias, M15 primary closed-candle confirmation, M5 precision that never triggers alone; score 0-100 with the requested weights (15/15/15/10/10/15/10/5/5), bands 70/80/90; TP1 + TP2, pip distance, ATR volatility gate, spread gate.
- Stage 3 (planned): backtests per pair with out-of-sample, session breakdown, costs; needs M5 history files from the phone (this sandbox has no market-data access).

- Stage 2 (done): `forex` profile in selective.py (M15-primary confirmation, forex_score with the requested weights, bands 70/80/90, TP1/TP2, pips, ATR/volatility/liquidity gates, one active signal per symbol). Stage 3 (backtests per pair) still needs M5 history files.

## Tightened filtering (latest)
Notify 82+, WATCH 75-81 console only, counter-trend 90+ & strong confirm, MSS+displacement+FVG required,
net R:R >= 1.5 with spread margin 1.25, UTC killzones London 07-11 / NY 12-16. 245 tests pass. Not measured on live results yet.

- Every entry alert now ends with a pre-entry checklist (bot-verified items + items you check yourself). SK system profile: waiting for the user's rules.

## SCALP / SWING modes
Strict modes implemented (see README). 253 tests pass. Zones for swing come from H1 only (H4 FVG/OB not used); M3 is built from M1. Not measured on live results.

## DAX40 added (opt-in)
Seven markets configured; DAX40 broker symbol and specification still unverified (null). Test each market with analyze_now.py on the phone before enabling alerts. 259 tests pass.
