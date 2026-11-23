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

## KENAN layout + VWAP/ADX/TP3 (stage 1 of the master upgrade)
Done: indicators.py (VWAP, ADX), TP3, KENAN layout, counter-trend gate 70. Not done yet: SQLite journal, named OB states, news stale/missing status, MT5 price validation, M5 listing check, backtests. 265 tests pass.

## Audit follow-up done
Notify 80, M15 confirmation mandatory (switch), news status + NEWS_STRICT, zone memory persisted, legacy doc fixed. 291 tests pass. Still open: SQLite journal, named OB states, MT5 price validation, M5 listing check, backtests (need M5 CSVs from the phone).

## Phase 2: six alert classes (done, 299 tests pass)
`alert_classes.py` (state machine, persistence, delivery), `selective.watch_observations`, `ict_alerts.track_setups`. Untested on live data; distinct sounds are vibration patterns plus spoken labels (no custom audio files). Next phases wait for approval: reversal tracker states, performance journal (MFE/MAE), scoring weights (needs real trades).

## Phase 3-4: reversal states + journal (done)
`reversal_tracker.py` (six states), `journal.py` + MFE/MAE in `tracker.py`. Still open: M5 listing gate, MT5 price validation (limited on Android), new scoring weights (needs real trades).

## M5 listing gate (checked, tested)
A scan waits ("waiting for the latest M5 candle to be listed") while the newest closed M5 candle is older than the last 5-minute boundary; feeds that stay stale are blocked by the STALE status. MT5 price validation is not possible on Android.

## Score 70 and H1 frame (done)
`MIN_NOTIFY_SCORE` default 70, `HTF_MODE` default h1m15 (all profiles except swing). To be judged only on real results.

## NY session engine, phase 1 (done, display only)
`ny_session.py` + `test_ny_session.py` (7 tests) + wiring in `ict_alerts.ny_view`. Next: market-state engine, IFVG/mitigation/OTE, RSI/MACD confirmation, performance reports by session, real backtests (needs M5 CSVs), then scoring weights. Tag `backup-before-ny-session` marks the state before this phase.

## Market state + IFVG + mitigation blocks, phase 2 (done, display only)
`market_state.py`, `structure.ifvgs`, `structure.mitigation_blocks`, `test_market_state.py` (5 tests) + wiring in `ict_alerts.state_view`. IFVG and mitigation blocks are not used by the entry decision yet. Tag `backup-before-market-state` marks the state before this phase.

## RSI / MACD, phase 3 (done, display only)
`indicators.rsi/macd/momentum_view`, shown in the KENAN signal. Tag `backup-before-rsi-macd`. Next: performance reports by session/market, then real backtests (no M5 CSVs yet: fetch_binance_history.py / fetch_dukascopy_history.py can create them on the phone).

## Reports by session / market / strategy, phase 4 (done)
`journal.py` (session_of, by-key report, auto-upgrade of old files), `tracker.py` writes session/profile/mode. Still open: real backtests (needs M5 CSVs, which the user must create on the phone), scoring weights after real trades.

## Deployment notes (done)
`DEPLOY_AR.md` (Termux steps, switches, reports). Startup now warns when the timezone database is missing (`pip install tzdata`). Per-market isolation: each market is scanned inside its own try/except in the main loop.

## Reversal state machine + arbitration + rejection log (done)
`reversal_machine.py`, `rejections.py`, `why.py`, `test_reversal_machine.py` (13 tests); `alert_classes.py` (suppressed setups end silently), `ict_alerts.py` (track_setups with arbitration, FINAL trail, rejection logging, defaults MAX_ACTIVE=1 / ALLOW_OPPOSITE=0), `selective.py` (ANALYSIS STAGES and HTF NOTE lines). Confirmation candle stays the closed M5 (user decision). Not measured on real results. Tag `backup-before-reversal-machine`.

## State flip + IFVG watch (done, analysis only)
`flip_ifvg.py`, `test_flip_ifvg.py` (9 tests), `alert_classes.EXTRA_CLASSES` (3 new alerts) and a persisted `meta` (flip cooldown). IFVG is not part of the FINAL signal decision. Tag `backup-before-ifvg-flip`.

## IFVG signal + decisions (done)
`flip_ifvg.py` (score, cost gate, FINAL layout, pending plans), `decisions.py`, `decide.py`, buttons in `alert_classes.deliver` and `ict_alerts.notify`, one open signal per market enforced through the signal store. IFVG is now part of the final decision (default on, `IFVG_SIGNALS=0` off) and is measured separately in the journal (profile `ifvg`). Not tested on live data: the Termux notification buttons run on the phone only. Tag `backup-before-ifvg-signal`.

- Structural Stop Loss for IFVG plans (Hard SL behind sweep/swing extreme + 0.5 ATR, Early Exit separate, entry at 50-75% depth, limit/market flag). 354 tests pass. Not yet measured on real trades.
- US mode: US100/US30 only reviewed in the New York open window (US_NY_WINDOW, US_MODE). 355 tests pass. Pattern filtering for the other two retracements is not done yet.
- US100/US30 removed from the default SYMBOLS (opt-in) at the user's request.
- US100/US30 back in default SYMBOLS, any hour; NY window gate is opt-in (US_MODE=1).
- Gold Asia-range breakout strategy added (asia_breakout.py, on by default, signals only; ASIA_BREAKOUT=0 turns off). 364 tests pass. Unmeasured: no backtest yet.
- Optional Twelve Data fallback for US100/US30 (TWELVEDATA_API_KEY). Unverified: symbol names and free-plan coverage/limits.
- Alerts are Arabic by default (ALERT_LANG=ar): titles, explanations, trade plan, buttons, tap-to-open full text. Terminal stage/report lines not translated yet.
- Periodic Arabic trades report (open / closed / per day, R and optional RISK_USD money). 379 tests pass.
- Termux screen Arabic fix: termfix.py shaping (needs pip install arabic-reshaper python-bidi), stage lines English. 383 tests pass. Shaping untested on a real phone.
- Default alert language is English again (Arabic opt-in with ALERT_LANG=ar); trades report has an English version. 384 tests pass.

## Entry-zones report + quality grade
Added `zones_report.py` (hook in `track_setups`, periodic report in the main loop, `ZONES_REPORT_MINUTES`), tests in `test_zones_report.py`. Full suite 387 passed. Not yet seen on a real phone.

## GBPUSD + USDJPY added
Default symbols, forex profile, news currencies, FX day start, markets.json specs (unverified). Tests updated. Not yet observed live.
