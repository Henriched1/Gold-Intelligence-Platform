# Phase 15 — Authenticated provider administration

## Implemented

- Provider inventory initializes with the application.
- Provider listing, registration and lifecycle updates require an admin bearer token.
- The token is read only from `ORACLE_FX_ADMIN_TOKEN`.
- The admin API remains disabled if the token is missing or shorter than 32 characters.
- Token comparisons use constant-time comparison.
- Provider registration accepts an environment-variable name, never a credential value.
- New providers remain `PENDING_REVIEW`.
- Lifecycle changes are limited to `PENDING_REVIEW` and `DISABLED`.
- Connection-test history is readable by an authenticated administrator.
- No public endpoint can write a successful connection-test result.
- No provider network request is performed by these routes.
- Licensing approval, data ingestion and signal eligibility remain separate gates.

## Admin routes

- `GET /v1/admin/providers`
- `POST /v1/admin/providers`
- `PATCH /v1/admin/providers/{provider_id}/lifecycle`
- `GET /v1/admin/providers/{provider_id}/connection-tests`

All routes require `Authorization: Bearer <admin-token>`.

## Deployment requirements

Configure `ORACLE_FX_ADMIN_TOKEN` as a secret environment variable in the hosting
platform. Use a randomly generated token of at least 32 characters and access
the API only over HTTPS. Never put the token in source control, logs, notes or
client-side application code.

This increment does not connect a provider, verify licensing, activate live
market data, or enable confirmed signals.
