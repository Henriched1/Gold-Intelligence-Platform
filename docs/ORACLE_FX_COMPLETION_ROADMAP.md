# ORACLE FX — Completion Roadmap

## Product objective

Build a dependable global market-intelligence system that studies
verified market data, explains current conditions, develops traceable
scenarios and identifies confirmed opportunities only when all
mandatory evidence is available.

This is a research and alerting system. Broker connectivity and trade
execution are outside the product scope.

## Existing foundation

The repository already contains the following capabilities:

- FastAPI backend and append-only forecast ledger.
- Canonical 30-instrument universe.
- D1, H4, H1 and M15 timeframe definitions.
- Structure, liquidity, displacement and FVG candidate analysis.
- Multi-timeframe and event-sequence engines.
- Scenario orchestration.
- Manual macro-event ledger and event-risk assessment.
- User-supplied cross-asset research.
- Historical candle storage, ingestion and quality reports.
- Provider licensing contracts and approval gates.
- Multi-provider registration and readiness matrix.
- Fail-closed confirmed-signal evidence gate.

Existing capabilities must be preserved and improved rather than
replaced without a migration and regression plan.

## Completion stages

### Phase 15 — Persistent provider onboarding

- Persist provider metadata and approved scope.
- Separate historical and live rights.
- Store credentials only in deployment secret storage.
- Add provider lifecycle and administrative controls.
- Record connection-test history and sanitized diagnostics.
- Never treat registration as licensing or connectivity approval.

### Phase 16 — Market-data pipeline

- Historical and live ingestion interfaces.
- Bounded scheduling and rate-limit handling.
- Idempotent storage and duplicate protection.
- Retry policy and provider failover.
- Quote, bid/ask and spread ingestion where licensed.
- Freshness, latency, completeness and provenance checks.
- Quarantine invalid or conflicting observations.

### Phase 17 — Market reference and sessions

- Verified instrument specifications and provider aliases.
- Trading sessions, holidays and daylight-saving transitions.
- Session-aware candle-close and freshness rules.
- Spread and quote validation.
- Instrument-specific market availability.

### Phase 18 — Automated macro intelligence

- Licensed economic-calendar ingestion.
- Actual, forecast, previous and revised figures.
- Central-bank events and official releases.
- Currency exposure and event windows.
- Source verification, timestamps and coverage monitoring.
- No automatic directional inference from a single release.

### Phase 19 — Advanced market intelligence

- Structure and liquidity engine improvements.
- External and internal liquidity mapping.
- BOS, CHoCH and displacement validation.
- FVG and order-block candidate tracking.
- CISD/MSS and retracement state.
- Volatility and market-regime classification.
- Session and intermarket context.
- Conflicting evidence detection.

### Phase 20 — Confirmed opportunity engine

- Automated candidate discovery.
- Higher-timeframe context and lower-timeframe trigger.
- Liquidity event and displacement evidence.
- Retracement and entry-zone validation.
- Invalidation, stop-loss and target calculations.
- Spread, event-risk and risk/reward checks.
- Candidate expiry and invalidation.
- Evidence-based explanations.
- WAIT whenever any mandatory check is missing.

### Phase 21 — Forecast validation laboratory

- Historical replay and point-in-time datasets.
- Look-ahead and survivorship-bias controls.
- Spread, commission, slippage, swap and gap assumptions.
- Walk-forward and untouched holdout testing.
- Baseline comparisons.
- Expectancy, drawdown, profit factor and average R.
- Sample sizes, confidence intervals and calibration.
- Performance segmentation by instrument, timeframe, session
  and market regime.
- No accuracy or probability claims without validation.

### Phase 22 — User application

- Responsive desktop and mobile dashboard.
- Global watchlist and instrument search.
- Market overview and charts.
- Scenario and evidence cards.
- Macro calendar and cross-asset views.
- Forecast, candidate and signal history.
- Clear source, freshness and verification status.

### Phase 23 — Alerts and monitoring

- Confirmed-only notifications.
- Candidate expiry and invalidation alerts.
- Economic-event and data-quality alerts.
- Duplicate suppression and delivery records.
- Provider outage and stale-feed monitoring.

### Phase 24 — Production hardening

- Automated CI and regression tests.
- Database migrations, backups and recovery.
- Authentication and authorization.
- API rate limiting and secure secret handling.
- Audit logs and operational monitoring.
- Performance, resilience and end-to-end testing.

## Release gates

A stage is not complete merely because code exists.

Each stage must have:

1. Tests for normal, invalid and failure cases.
2. Regression tests for existing functionality.
3. Documentation of limitations and data provenance.
4. No secrets or unapproved provider calls in source control.
5. A passing complete test suite before commit and push.

## Current live-data boundary

Provider rights, credentials, market coverage and connectivity remain
unverified until independently configured and tested. No provider
coverage, live connection, predictive accuracy or signal eligibility
may be claimed from registry metadata alone.
