# ORACLE FX — Global Market Intelligence Architecture

## Product scope
- Markets: global FX, precious metals, major equity indices, energy and selected liquid crypto.
- Intelligence: technical, fundamental, macroeconomic, intermarket and statistical/AI research.
- Alerts: confirmed opportunities only; no early-entry alerts.
- Execution: research and alerts only. No broker connection, order placement, account access or live trading.

## Design principles
1. Every finding carries source, timestamp, instrument, timeframe and freshness.
2. No fabricated prices, events, probabilities or confirmations.
3. Fail closed: stale, missing, conflicting or low-quality inputs mean DATA INCOMPLETE / WAIT.
4. Probabilities remain unavailable until calibrated out-of-sample validation exists.
5. Separate observations, hypotheses, forecasts and confirmed setups.
6. Preserve existing structural engines and append-only forecast ledger.

## Logical components
- API and presentation: FastAPI initially; dashboard and push notifications later.
- Orchestration: schedules collection, normalizes timestamps/symbols, validates freshness and coordinates engines.
- Data adapters: market feed, historical candles, economic calendar, central-bank releases, DXY, rates/yields and cross-asset references. Each adapter declares licensing, latency and coverage.
- Canonical market store: normalized OHLCV, bid/ask where available, source metadata, calendars and raw snapshots.
- Technical intelligence: structure, liquidity pools/sweeps, displacement, FVG, order-block candidates, volatility and multi-timeframe context.
- Macro intelligence: scheduled releases, actual/consensus/previous/revisions, central-bank communications and event-risk windows.
- Intermarket intelligence: USD index, Treasury/real yields where available, correlated assets and rolling correlation/beta by regime.
- Regime intelligence: trend/range, volatility state, session/liquidity conditions and structural transition.
- Scenario engine: bullish/base/bearish scenarios, invalidation levels, target liquidity and traceable evidence.
- Entry gate: deterministic checklist; confirmed-only alerts when all mandatory conditions and risk/data checks pass.
- Validation: replay/backtests, walk-forward evaluation, calibration, expectancy, drawdown and drift monitoring.
- Forecast ledger: append-only inputs, decisions, horizon and outcome evaluations.
- Alerts: deduplication, expiry, quiet periods and delivery receipts; alerts never execute trades.

## Global market universe
Use provider-neutral canonical IDs. Start with liquid FX majors/crosses, gold/silver, selected liquid indices/energy and selected crypto pairs. Provider-specific aliases must be explicitly configured against verified contract specifications. Never assume XAUUSD, GOLD, XAUUSDm or suffix variants are interchangeable. Enable a market only after verifying data coverage, session calendar, spread and historical depth.

## Confirmed-only signal contract
Every candidate requires fresh and complete aligned data; higher-timeframe context and invalidation; a documented liquidity event or alternative setup; displacement and structure confirmation; retracement into a predefined POI; lower-timeframe trigger; acceptable spread, volatility, event risk and reward-to-risk; no unresolved material conflict; and a reproducible evidence record. Missing any mandatory item means WAIT / NO SIGNAL. A score cannot override a failed mandatory gate.

## Macro event model
Persist event ID, currency/region, scheduled UTC time, source, impact, actual, consensus, previous, revised previous, units, release timestamp and source URL. Do not infer direction from one number: compare surprise, revisions, expectations and instrument sensitivity. Uncorroborated headlines remain unverified.

## Forecast validation
Use point-in-time data; prevent look-ahead and survivorship bias; use walk-forward and untouched holdout periods; include spread, commission, slippage, swaps and session gaps; report sample size, win rate, expectancy, profit factor, drawdown, average R, calibration and confidence intervals; compare simple baselines; segment by instrument, timeframe, session and regime. No accuracy claim or probability display without sufficient independent observations and calibration.

## Phases
1. Foundation: schemas, universe, provider interfaces, health/freshness and audit metadata.
2. Data: live and historical feeds plus economic calendar ingestion; persist raw and normalized records.
3. Intelligence: wire existing SMC engines; add macro, intermarket, volatility and regime engines.
4. Decisions: scenarios, mandatory confirmed-only gate and human-readable explanations.
5. Validation: replay/backtest, walk-forward evaluation, forecast scoring and drift reports.
6. Alerts/dashboard: confirmed-only notifications, watchlists, evidence cards and operator controls.

## Current implementation boundary
The existing repository has a FastAPI foundation, SQLite append-only forecast ledger, XAUUSD candle-analysis endpoints and technical structure/event-sequence engines. It does not yet demonstrate connected live data, global provider coverage, macro ingestion, intermarket feeds, validated predictive models or alert delivery. These remain integration tasks.
