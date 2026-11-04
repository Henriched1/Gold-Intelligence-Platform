# Phase 12 — Independent global data foundation

## Changes
- The provider-neutral instrument contract and historical candle store now accept the enabled canonical global universe.
- Provider adapters must still declare their own supported instruments, mappings, timeframes, session conventions, price components and licensing status.
- Twelve Data remains restricted to the instrument/timeframe scope in its written approval record. This phase does not broaden that approval.
- Added `GET /v1/data-sources/readiness`. It inspects local adapter metadata and credential presence only; it makes no provider request and never returns credential values.
- Added `POST /v1/cross-asset/analyse` for descriptive pairwise Pearson correlation of exact timestamp-aligned log returns.
- Caller-supplied cross-asset observations are labelled `USER_SUPPLIED_UNVERIFIED`.

## Safety and limitations
- No provider network calls, live market feed, broker connection, order submission or execution are enabled.
- A configured credential is not proof of provider connectivity, data entitlement or licensing rights.
- Global historical storage capability does not mean a live feed exists for every instrument.
- Cross-asset correlations are descriptive, not causal and not predictive.
- No probability, directional trading bias, confirmed entry, alert or execution is generated.
- Existing macro, market-structure and event-sequence modules remain in place.
