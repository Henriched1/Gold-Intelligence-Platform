# Phase 8 — Twelve Data historical provider pilot

## Status

The Twelve Data adapter is implemented and tested with mocked responses.

It is NOT approved for production data retrieval. No real API request has
been made, no provider licensing approval has been recorded, and no
historical candles have been imported through this adapter.

The adapter remains fail-closed until a valid private approval record
is supplied.

## Scope

- Canonical instrument: XAUUSD
- Provider symbol: XAU/USD
- Supported timeframes: M15, H1, H4, D1
- Endpoint: Twelve Data time_series
- Maximum response batch: 5,000 candles
- API key environment variable: TWELVE_DATA_API_KEY
- Authentication: Authorization header
- Storage: existing provider-neutral ingestion framework
- Execution: research and historical-data ingestion only

No broker connection, order placement, or live trading is included.

## Licensing gate

The private file is:

    config/provider_approvals.json

It is excluded from Git. The example template is:

    config/provider_approvals.example.json

Approval must document:
- Commercial use
- Non-US market-data permission
- Historical storage rights
- Derived-data storage rights
- Derived-data display rights
- Permitted instruments and timeframes
- Exchange timezone
- Retention period
- Attribution requirements
- Approval reference and expiry

Raw-data redistribution must remain disabled.

A paid API subscription alone must not be treated as proof of these rights.
Written permission must be verified before enabling real retrieval.

## Data handling

- Intraday request timestamps are sent in UTC.
- Daily request timestamps use the approved exchange timezone.
- Returned timestamps are converted to UTC.
- Candles outside the requested range are rejected.
- Duplicate and non-chronological candles are rejected.
- OHLC and volume validation is performed by the canonical normaliser.
- Missing volume remains unavailable; explicit zero remains available.
- Provider provenance and price-component uncertainty are preserved.
- The adapter does not write directly to the historical database.

The 5,000-row provider response limit is a hard batch boundary.
Requests requiring more history must be split into bounded batches by
a separately controlled caller. The adapter must not silently claim
that a capped response contains the entire requested history.

## Security

Set TWELVE_DATA_API_KEY in the runtime environment. Never commit the
key, include it in an approval file, or paste it into chat.

The adapter sends the key in the Authorization header, not the URL.
Provider network exceptions are converted to safe error messages.

## Verification

Run the adapter tests:

    PYTHONPATH=backend python -m unittest backend.tests.test_twelve_data_provider -v

Run the full test suite:

    PYTHONPATH=backend python -m unittest discover -s backend/tests -v

Tests use mocked responses. They do not verify the user's API credentials,
provider account entitlement, commercial licensing, or live endpoint
availability.

## Next approval steps

1. Obtain written confirmation of all required data rights.
2. Record the approved scope, timezone, attribution, retention and expiry
   in the private approval file.
3. Configure the API key securely in the runtime environment.
4. Verify the provider account and endpoint with a small isolated request.
5. Import into an isolated test database first.
6. Run the historical data-quality report.
7. Review provenance, coverage, missing volume and source consistency.
8. Only then consider a controlled production historical-data import.
