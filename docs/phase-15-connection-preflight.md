# Phase 15 — Provider connection preflight

The authenticated endpoint
`POST /v1/admin/providers/{provider_id}/connection-test`
performs local checks only.

It checks provider lifecycle, credential presence, local adapter registration,
adapter initialization, adapter identity, licensing metadata, every declared
historical instrument mapping, historical timeframe coverage, and separate
live timeframe coverage.

It records append-only history with outcome `NOT_RUN`.

This endpoint does not contact a provider. It never exposes credential values,
does not grant licensing approval, and never makes a provider signal-eligible.

`READY_FOR_EXPLICIT_NETWORK_TEST` means only that local configuration and
declared-scope checks passed. It does not authorize a network request or prove
that the provider is reachable.

Actual network verification requires a separately reviewed provider-specific
method, documented data rights, configured secrets, and explicit authorization.
