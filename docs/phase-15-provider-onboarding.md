# Phase 15 — Persistent provider onboarding foundation

## First increment

- Persistent provider inventory in the existing ORACLE FX SQLite database.
- Stable provider IDs, adapter keys and supported instrument/timeframe scopes.
- Historical and live scopes are separate; live scope must be contained in
  historical scope.
- Only an environment-variable name is stored for credentials. Secret values
  are never accepted by this storage API.
- New providers always start in `PENDING_REVIEW`.
- Lifecycle controls allow `PENDING_REVIEW` and `DISABLED` only.
- Append-only connection-test history stores bounded outcomes, timestamps,
  optional latency, credential-presence status and sanitized diagnostic codes.
- A successful connection test does not grant licensing approval or signal
  eligibility.
- This storage layer performs no network requests.

## Boundaries

This increment does not activate Twelve Data, perform live connection tests,
change licensing approval records, enable ingestion, or modify confirmed-signal
gates. Connection-test records must only be written after an actual test has
been implemented and explicitly authorized.

The existing provider approval contract remains authoritative for ingestion
permissions. Onboarding metadata is administrative inventory, not evidence
of contractual data rights.

## Next increment

Add authenticated administrative endpoints, reconcile onboarding metadata
with adapter registration and licensing approvals, and implement a bounded
connection-test harness. The harness must redact provider responses and
credentials, make no request unless explicitly enabled, and record only
sanitized outcomes.
