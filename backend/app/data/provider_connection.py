"""Local-only provider configuration preflight. Never performs network I/O."""

import os

from app.data import provider_onboarding as store
from app.data import provider_registry as registry


def _as_scope(value):
    if isinstance(value, (set, frozenset, list, tuple)):
        return set(value)
    return set()


def _metadata_diagnostic(provider_id, provider, metadata):
    if metadata is None:
        return "ADAPTER_METADATA_INVALID"

    if getattr(metadata, "provider_id", None) != provider_id:
        return "ADAPTER_PROVIDER_MISMATCH"

    if getattr(metadata, "licensing_status", None) != "APPROVED":
        return "LICENSE_NOT_APPROVED"

    mapping = getattr(metadata, "instrument_mapping", None)
    if not isinstance(mapping, dict):
        return "ADAPTER_METADATA_INVALID"

    if any(
        not isinstance(mapping.get(symbol), str)
        or not mapping[symbol].strip()
        for symbol in provider["supported_instruments"]
    ):
        return "HISTORICAL_SCOPE_UNSUPPORTED"

    historical = _as_scope(
        getattr(metadata, "supported_timeframes", None)
    )
    required_historical = set(provider["historical_timeframes"])

    if not required_historical.issubset(historical):
        return "HISTORICAL_SCOPE_UNSUPPORTED"

    live = _as_scope(
        getattr(metadata, "live_supported_timeframes", None)
    )
    required_live = set(provider["live_timeframes"])

    if not required_live.issubset(live):
        return "LIVE_SCOPE_UNSUPPORTED"

    return "NETWORK_TEST_NOT_ENABLED"


def run_provider_connection_preflight(provider_id, environ=None):
    provider = store.get_provider(provider_id)
    if provider is None:
        raise KeyError("provider not found")

    environment = os.environ if environ is None else environ
    credential_name = provider["credential_env_var"]
    credential_present = bool(
        isinstance(environment.get(credential_name), str)
        and environment.get(credential_name).strip()
    )

    diagnostic = None

    if provider["lifecycle_state"] == "DISABLED":
        diagnostic = "PROVIDER_DISABLED"
    elif not credential_present:
        diagnostic = "CREDENTIAL_MISSING"
    else:
        registry.ensure_default_providers()
        factory = registry.get_registered_provider_factories().get(
            provider_id
        )

        if factory is None:
            diagnostic = "ADAPTER_UNAVAILABLE"
        else:
            try:
                adapter = factory()
            except Exception:
                adapter = None
                diagnostic = "ADAPTER_INITIALIZATION_FAILED"

            if adapter is not None:
                if getattr(adapter, "credentials_configured", False) is not True:
                    diagnostic = "CREDENTIAL_MISSING"
                else:
                    diagnostic = _metadata_diagnostic(
                        provider_id,
                        provider,
                        getattr(adapter, "metadata", None),
                    )

    history = store.record_connection_test(
        provider_id=provider_id,
        outcome="NOT_RUN",
        credential_present=credential_present,
        diagnostic_code=diagnostic,
    )

    scope_validated = diagnostic == "NETWORK_TEST_NOT_ENABLED"

    return {
        "provider_id": provider_id,
        "preflight_status": (
            "READY_FOR_EXPLICIT_NETWORK_TEST"
            if scope_validated else "BLOCKED"
        ),
        "connection_test": history,
        "adapter_scope_validated": scope_validated,
        "connection_verified": False,
        "network_request_performed": False,
        "network_test_authorized": False,
        "licensing_approved": False,
        "signal_eligible": False,
        "message": (
            "Local configuration and declared scope checks passed. "
            "No provider connection was attempted."
            if scope_validated
            else "Preflight is blocked. Review the diagnostic code."
        ),
    }
