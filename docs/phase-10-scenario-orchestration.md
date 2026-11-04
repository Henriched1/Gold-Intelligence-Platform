# Phase 10 — Global scenario orchestration

## API
- GET /v1/universe lists registered canonical instruments.
- POST /v1/global/analyse analyses supplied D1, H4, H1 and M15 candles.

## Engine integration
The scenario orchestrator combines the existing multi-timeframe and event-sequence engines. It reports bullish and bearish structural scenarios independently and applies the confirmed-only evidence gate.

## Safety boundary
User-supplied candles do not prove provider identity, licensing, freshness or exchange-session provenance. The current implementation has no connected global live feed, macro calendar, verified bid/ask spread, post-retracement entry trigger or proposed entry/stop/target.

Those missing mandatory checks remain false. A structural sequence therefore cannot become a confirmed signal through this API.

No trade execution, broker connection, alert delivery or forecast probability is enabled. Existing XAUUSD analysis and historical acquisition modules are retained.

A completed sequence is reported only in `observed_sequence_directions`.
`selected_direction` remains null and `direction_selection` is
`NONE_RESEARCH_ONLY`. This research API does not select a trading direction.
