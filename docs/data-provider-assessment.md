# ORACLE FX — Historical Data Provider Assessment

**Phase:** 6 — Provider Research and Integration Planning
**Date:** 2026-11-03
**Initial instrument:** XAUUSD
**Status:** Research completed; provider approval pending

## 1. Objective

Identify a reliable and legally usable historical market-data provider for
XAUUSD research.

Required timeframes:
- D1
- H4
- H1
- M15

The system remains research-only. This phase does not enable broker
connections, trade execution, or validated forecast probabilities.

## 2. Provider assessment

### Dukascopy

Technical strengths:
- Historical bid/ask tick data is documented.
- Historical data export documentation describes an AWS S3
  Requester Pays archive.
- Third-party dukascopy-node documentation supports XAUUSD and
  tick/candle downloads, including M15, H1, H4 and D1.
- Tick data can support independent candle aggregation and
  bid/ask spread research.

Risks and open questions:
- Requester Pays means download and infrastructure costs must be assessed.
- The published XML-data agreement is non-commercial and temporary.
  It must not be assumed to license the separate historical tick archive.
- Written confirmation is required for the intended storage, analysis,
  derived-data display and commercial use.
- Provider-specific session conventions and volume meaning must be recorded.

Assessment: TECHNICALLY PROMISING; LICENSING NOT APPROVED.

### Twelve Data

Technical strengths:
- Documents Gold Spot / USD as XAU/USD.
- Commodity catalogue documents historical and intraday coverage.
- Time-series API provides OHLC and volume fields for non-currency instruments.
- REST API is suitable for a provider adapter.

Risks and open questions:
- Published plan limits, request quotas and pricing must be checked before use.
- Its commodity marketing page and support policy need written clarification
  for the exact intended business use.
- Confirm commercial display, derived-data display, caching, retention,
  redistribution and non-US market-data permissions.
- Confirm exact XAU/USD historical depth and availability for every required
  interval under the proposed subscription.

Assessment: API-CONVENIENT; COMMERCIAL RIGHTS AND PLAN REQUIRE CONFIRMATION.

### OANDA

Technical strengths:
- v20 API documents historical candle retrieval.
- Supports midpoint, bid and ask candle components.
- Historical pricing can provide an independent comparison dataset.

Risks and open questions:
- Eligible v20 account and API access are required.
- Instrument availability depends on account/division.
- OANDA documents differences between historical candle pricing and
  account-specific live pricing.
- Confirm XAU_USD availability, history depth, rate limits and data-use rights.

Assessment: POTENTIAL SECONDARY COMPARISON SOURCE; NOT YET APPROVED.

## 3. Preliminary decision

No provider is approved for production ingestion at this stage.

Dukascopy is the first technical candidate to investigate for historical
research, subject to written licensing clarification and cost assessment.

Twelve Data is the first API-oriented alternative to evaluate.

OANDA remains a possible independent comparison source where account
eligibility and instrument access are confirmed.

No provider should be selected solely on price, API convenience or apparent
historical depth.

## 4. Provider approval checklist

Before production integration, obtain and record:

1. Written permission for the intended use.
2. XAUUSD/XAU-USD/XAU_USD instrument identity and pricing methodology.
3. Earliest available timestamp for D1, H4, H1 and M15.
4. Whether candles are bid, ask, midpoint or another price construction.
5. Timestamp timezone and candle-boundary/session convention.
6. Meaning, units and availability of volume.
7. Missing-candle and market-closure behaviour.
8. API rate limits, download limits and backfill restrictions.
9. Subscription price, overage costs and infrastructure costs.
10. Storage, caching and retention permissions.
11. Commercial display and derived-data rights.
12. Redistribution restrictions and attribution requirements.
13. Provider support, uptime commitments and revision policy.

A provider remains NOT_APPROVED until these items are sufficiently resolved.

## 5. Planned adapter architecture

Each provider will implement a provider-neutral interface.

Conceptual contract:

- provider_id
- provider_version
- instrument_mapping
- supported_timeframes
- fetch_candles(start, end, timeframe)
- session_convention
- price_component
- volume_semantics
- licensing_status

The adapter must return raw provider observations and metadata.
A separate normaliser will convert observations into the canonical candle
schema expected by the historical store.

Provider-specific parsing must not leak into the analysis engines.

## 6. Canonical ingestion rules

- Store timestamps in UTC with explicit timezone conversion.
- Validate finite OHLC values and high/low consistency.
- Preserve provider identity and source version.
- Preserve session convention and price component.
- Preserve volume availability separately from volume value.
- Do not interpret missing volume as confirmed zero volume.
- Do not merge different providers into one synthetic candle series.
- Do not overwrite existing source observations.
- Reject conflicting duplicates from the same provider.
- Record ingestion failures without silently skipping bad data.
- Do not fabricate, interpolate or automatically repair missing candles.
- Classify session gaps only after an explicit provider session calendar exists.

## 7. Cross-provider validation

When a second provider is approved:

- Compare only matching instrument, timeframe and timestamp boundaries.
- Compare bid with bid, ask with ask, or midpoint with midpoint.
- Do not compare incompatible price components as if they were identical.
- Report OHLC differences without automatically selecting a winner.
- Keep statistical anomaly baselines separate for each provider.
- Investigate session boundaries, daylight-saving changes and price spikes.
- Preserve both original datasets for reproducibility.

## 8. Pilot ingestion plan

After approval:

1. Fetch a small, bounded XAUUSD sample.
2. Validate response structure and timestamp interpretation.
3. Import into an isolated test database first.
4. Run the Phase 5 quality report.
5. Compare overlapping observations against an independent source.
6. Verify idempotent re-ingestion and conflicting-duplicate rejection.
7. Test provider outage, rate-limit and malformed-response handling.
8. Approve larger historical backfill only after review.

No live forecast evaluation should use a dataset that has not passed
documented quality and provenance checks.

## 9. Security and operational rules

- API credentials belong in environment variables or a secrets manager.
- Never commit credentials, tokens or account identifiers.
- Never print secrets in logs or API error responses.
- Use bounded requests, retries with backoff and explicit timeouts.
- Record request windows and ingestion outcomes.
- Keep provider access separate from analysis and forecast code.
- Do not connect a broker or enable trade execution.

## 10. Official references

Dukascopy historical data export:
https://www.dukascopy.com/wiki/en/development/data-export/

Dukascopy XML data-use agreement:
https://www.dukascopy.com/plugins/cont.php?ref_id=1273

Dukascopy historical download tooling documentation:
https://www.dukascopy-node.app/downloading-tick-data

Twelve Data commodity catalogue:
https://twelvedata.com/exchanges/commodity

Twelve Data API documentation:
https://twelvedata.com/docs

Twelve Data commercial and personal usage:
https://support.twelvedata.com/en/articles/5332349-commercial-and-personal-usage

Twelve Data pricing:
https://twelvedata.com/pricing

OANDA v20 API introduction:
https://developer.oanda.com/rest-live-v20/introduction/

OANDA candle endpoint:
https://developer.oanda.com/rest-live-v20/pricing-ep/

## 11. Phase 6 exit criteria

Phase 6 is complete when:
- Provider candidates and limitations are documented.
- Intended usage and licensing questions are recorded.
- A provider-neutral integration design is defined.
- Canonical ingestion and validation rules are documented.
- No unapproved data source has been connected.

Production provider approval and implementation are separate gates.
