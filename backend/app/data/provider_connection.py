"""Local-only provider connection preflight. Never performs network I/O."""

import os

from app.data import provider_onboarding as store
from app.data import provider_registry as registry


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
        factory = registry.get_registered_provider_factories().get(provider_id)

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
                    metadata = getattr(adapter, "metadata", None)
                    if getattr(metadata, "licensing_status", None) != "APPROVED":
                        diagnostic = "LICENSE_NOT_APPROVED"
                    else:
                        diagnostic = "NETWORK_TEST_NOT_ENABLED"

    history = store.record_connection_test(
        provider_id=provider_id,
        outcome="NOT_RUN",
        credential_present=credential_present,
        diagnostic_code=diagnostic,
    )
    ready = diagnostic == "NETWORK_TEST_NOT_ENABLED"

    return {
        "provider_id": provider_id,
        "preflight_status": (
            "READY_FOR_EXPLICIT_NETWORK_TEST" if ready else "BLOCKED"
        ),
        "connection_test": history,
        "connection_verified": False,
        "network_request_performed": False,
        "licensing_approved": False,
        "signal_eligible": False,
        "message": (
            "Local checks passed. No provider connection was attempted."
            if ready else "Preflight is blocked. Review the diagnostic code."
        ),
    }
