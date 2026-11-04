# Phase 14 — Independent live market-data infrastructure

## Step 1: provider capability and live-rights separation

- Added a global instrument/timeframe readiness matrix using the canonical universe.
- Provider mappings, historical permissions, live permissions and credential presence are reported separately.
- Historical provider approval does not imply live-market-data access.
- Live access requires explicit `live_market_data_access` and `live_data_storage` permissions in the written provider approval record.
- Twelve Data remains mapped only to XAUUSD. No permissions or symbols are inferred for other markets.
- Credential state is exposed only as a boolean; secret values are never returned.
- Readiness inspection performs zero network requests.

## Current limitations

- No live connection test has been performed.
- No live data is connected or signal-eligible.
- A provider mapping is not evidence of market-data entitlement.
- Each additional provider requires its own adapter, written rights, symbol mapping, session convention and validation.
- Broker connection and trade execution remain disabled.

## Next steps

1. Select a provider with documented rights for the required global markets.
2. Obtain written historical and live-data permissions for each instrument and timeframe.
3. Configure credentials through the deployment secret store, never source control.
4. Only after explicit authorization, perform a bounded connection/authentication test.
5. Verify timestamps, completeness, source provenance, spread availability and freshness before enabling any signal eligibility.
