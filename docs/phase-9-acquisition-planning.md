# Phase 9 — Historical acquisition planning and controlled pilot

## Status

Phase 9 implementation is complete at the code and automated-test level.

Implemented components:

1. Deterministic historical acquisition planner.
2. Bounded request chunks, with a maximum of 5,000 requested rows per chunk.
3. UTC-aligned intraday boundaries for M15, H1 and H4.
4. Exchange-timezone daily boundaries with daylight-saving-aware planning.
5. Deterministic SHA-256 plan identifiers.
6. Isolated SQLite pilot database, separate from production historical storage.
7. Append-only pilot candle storage with provenance and conflict protection.
8. Controlled acquisition runner with dry-run as the default.
9. Approval-gated provider execution.
10. Bounded, resumable chunk execution and checkpoint tracking.
11. Per-chunk outcomes, including returned rows, duplicates and failures.
12. Plan-scoped pilot quality reporting.
13. Regression tests for acquisition, recovery, data isolation and quality reporting.

The implementation is tested with mocked provider responses. It has not been authorized to retrieve real market data.

## Supported initial scope

- Instrument: XAUUSD
- Timeframes: D1, H4, H1, M15
- Initial provider candidate: Twelve Data
- Maximum request size: 5,000 rows per chunk

## Request planning rules

### Intraday

M15, H1 and H4 ranges require timezone-aware timestamps aligned to UTC candle boundaries.

Chunks are contiguous and non-overlapping. Each chunk contains no more than the configured row limit.

### Daily

D1 plans require an explicit exchange timezone. Calendar dates are partitioned using that timezone, including daylight-saving transitions.

The timezone used for real acquisition must come from a verified provider approval record. The planner's UTC default is not proof of the provider's daily candle timezone.

## Pilot database and data integrity

Pilot acquisition uses a separately specified SQLite database.

The pilot store:
- Rejects the production historical database path.
- Records plans and individual chunk states.
- Stores canonical candle data and source provenance.
- Prevents updates and deletions to stored pilot candles.
- Treats identical replays as duplicates.
- Rejects conflicting candle or provenance records.
- Does not silently replace existing historical data.

Pilot data is not automatically imported into production historical storage.

## Controlled execution and recovery

The runner defaults to dry-run mode.

Real provider execution requires explicit execution authorization and a valid approval record. Completed chunks are skipped during resume. Failed or running chunks require explicit resume authorization.

Execution outcomes distinguish:
- Chunks registered.
- Network requests attempted.
- Candles inserted during the current run.
- Identical duplicate candles encountered.
- Final chunk completion status.

`market_data_written` indicates whether the current run inserted new candles. It does not indicate whether the pilot database already contains matching data from an earlier run.

## Quality reporting

The pilot quality report:
- Scopes candle records to the selected plan, instrument and timeframe.
- Uses actual planned chunk boundaries, including expanded daily boundaries.
- Counts unique observed timestamps.
- Reports repeated timestamp rows across sources.
- Separately reports provider rows returned and replayed duplicate rows.
- Calculates unverified time-grid slots using unique timestamps.
- Does not infer missing market candles from weekends, holidays or session closures without an approved session calendar.
- Does not modify stored records.

A quality report is a review aid, not proof that a provider's historical data is complete or accurate.

## Licensing and safety boundaries

The Twelve Data adapter is not authorized for real acquisition until written licensing approval is obtained and independently verified.

The approval record must establish:
- Commercial-use permission.
- Non-US market-data approval where applicable.
- Historical-data storage permission.
- Derived-data storage and display permission.
- Instrument and timeframe scope.
- Exchange timezone and retention requirements.
- Required attribution.
- No raw-data redistribution permission.

No real API key or approval is assumed to exist.

Until approval is verified:
- Do not make real provider requests.
- Do not import unapproved market data.
- Do not bypass approval checks.
- Do not connect a broker or execute trades.
- Do not generate synthetic candles or silently fill gaps.
- Keep production historical storage untouched.

## Verification

Compile the data modules:

    python -m py_compile backend/app/data/*.py backend/app/data/providers/*.py

Run the complete backend test suite:

    PYTHONPATH=backend python -W error::ResourceWarning -m unittest discover -s backend/tests -v

Latest recorded verification: 142 tests passed, with ResourceWarnings treated as errors.

## Remaining work before real acquisition

Phase 9 code implementation is complete. Operational authorization remains separate:

1. Obtain and verify written market-data licensing approval.
2. Configure the provider API key securely in the local environment.
3. Confirm the approved instrument, timeframe, timezone and retention scope.
4. Run a small authorized pilot against the isolated pilot database.
5. Review the acquisition and quality reports before considering any production import.

No real market-data acquisition or production import is authorized by this implementation alone.
