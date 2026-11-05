# Phase 16 — Step 1: Bounded historical ingestion orchestration

## Purpose

Add an explicit orchestration layer around the existing historical
provider ingestion service without replacing its licensing, timestamp,
normalisation, provenance or storage controls.

## Behaviour

- Providers are supplied explicitly in priority order.
- Maximum provider count: 5.
- Maximum attempts per provider: 3.
- Default attempts per provider: 2.
- Exponential retry delay is capped at 10 seconds.
- Only provider rate-limit and temporary-unavailability errors retry.
- Exhausted transient failures may move to the next supplied provider.
- Licensing failures stop the pipeline.
- Invalid provider responses stop the pipeline.
- Provider exception messages and response bodies are not returned.
- Provider-specific approval-file paths may be supplied.

## Safety boundary

Importing this module does not start a scheduler or background worker.
No provider is contacted until `ingest_with_failover` is explicitly
called with provider instances and a request.

Each actual fetch still passes through `ingest_provider_batch`, which
checks provider metadata and written licensing approval before fetching,
normalises the response, rejects invalid or future candles, checks the
requested range, and stores provenance through the existing historical
store.

This increment does not add live subscriptions, quote storage, automatic
scheduling, or trading signals. It does not grant licensing approval or
signal eligibility.

## Retry policy

Only `ProviderRateLimited` and `ProviderUnavailable` are retryable.
`ProviderNotApproved` and `ProviderResponseError` stop processing
immediately. Unexpected programming errors are not hidden.

Attempt outcomes returned by this service are bounded, sanitized
in-memory diagnostics. Persistent pipeline job and attempt history,
rate-limit-aware scheduling, provider health scoring and live quote
ingestion remain later Phase 16 work.

## Tests

Tests use simulated providers and injected ingestion/sleep functions.
They perform no outbound network requests.
