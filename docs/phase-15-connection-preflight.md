# Phase 15 — Provider connection preflight

The authenticated endpoint
`POST /v1/admin/providers/{provider_id}/connection-test`
performs local preflight checks only.

It checks provider lifecycle, credential presence, local adapter registration,
adapter initialization, and the adapter's existing licensing status metadata.
It records an append-only history item with outcome `NOT_RUN`.

This endpoint does not contact a provider. It never exposes credential values,
does not grant licensing approval, and never makes a provider signal-eligible.
Even `READY_FOR_EXPLICIT_NETWORK_TEST` means only that local checks passed.

An actual network test requires a separately reviewed provider-specific method,
documented data rights, configured secrets, and explicit authorization.
