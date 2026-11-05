"""Bounded historical ingestion orchestration with explicit provider failover.

No scheduler or background worker is started by importing this module.
Provider calls occur only when ingest_with_failover is explicitly invoked.
"""

import math
import time

from app.data.provider_contract import (
    ProviderError,
    ProviderNotApproved,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderUnavailable,
)
from app.data.provider_ingestion import ingest_provider_batch


MAX_PROVIDERS = 5
MAX_ATTEMPTS_PER_PROVIDER = 3
MAX_BACKOFF_SECONDS = 10.0


def _validate_options(
    providers,
    approval_paths,
    max_attempts_per_provider,
    backoff_base_seconds,
    backoff_cap_seconds,
):
    if not isinstance(providers, (list, tuple)) or not providers:
        raise ValueError("providers must be a non-empty list or tuple")

    if len(providers) > MAX_PROVIDERS:
        raise ValueError("provider count exceeds the configured limit")

    provider_ids = []
    for provider in providers:
        metadata = getattr(provider, "metadata", None)
        provider_id = getattr(metadata, "provider_id", None)

        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("every provider must have a valid provider_id")

        provider_ids.append(provider_id)

    if len(set(provider_ids)) != len(provider_ids):
        raise ValueError("duplicate provider_id values are not allowed")

    if (
        not isinstance(max_attempts_per_provider, int)
        or isinstance(max_attempts_per_provider, bool)
        or not 1 <= max_attempts_per_provider <= MAX_ATTEMPTS_PER_PROVIDER
    ):
        raise ValueError("max_attempts_per_provider must be between 1 and 3")

    for value, field in (
        (backoff_base_seconds, "backoff_base_seconds"),
        (backoff_cap_seconds, "backoff_cap_seconds"),
    ):
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0
            or value > MAX_BACKOFF_SECONDS
        ):
            raise ValueError(f"{field} must be between 0 and 10")

    if backoff_base_seconds > backoff_cap_seconds:
        raise ValueError("backoff base must not exceed the backoff cap")

    if approval_paths is not None and not isinstance(approval_paths, dict):
        raise ValueError("approval_paths must be a provider-to-path mapping")


def ingest_with_failover(
    providers,
    request,
    approval_paths=None,
    max_attempts_per_provider=2,
    backoff_base_seconds=0.25,
    backoff_cap_seconds=2.0,
    *,
    ingest_fn=None,
    sleep_fn=None,
):
    """Try explicitly ordered providers with bounded transient retries.

    Licensing failures and invalid provider responses stop the pipeline.
    Only rate-limit and temporary-unavailability errors are retried or
    allowed to proceed to the next explicitly supplied provider.

    The existing ingestion function remains responsible for validating
    approval, normalising candles, checking timestamps and storing data.
    """
    _validate_options(
        providers,
        approval_paths,
        max_attempts_per_provider,
        backoff_base_seconds,
        backoff_cap_seconds,
    )

    ingest = ingest_fn or ingest_provider_batch
    pause = sleep_fn or time.sleep
    paths = approval_paths or {}
    attempts = []

    for provider_index, provider in enumerate(providers):
        provider_id = provider.metadata.provider_id
        approval_path = paths.get(
            provider_id,
            "config/provider_approvals.json",
        )

        for attempt_number in range(1, max_attempts_per_provider + 1):
            try:
                result = ingest(
                    provider,
                    request,
                    approval_path=approval_path,
                )
                attempts.append({
                    "provider_id": provider_id,
                    "attempt": attempt_number,
                    "outcome": "INGESTED",
                    "diagnostic_code": None,
                })
                return {
                    "status": "INGESTED",
                    "provider_id": provider_id,
                    "failover_used": provider_index > 0,
                    "attempts": attempts,
                    "ingestion": result,
                }

            except ProviderRateLimited:
                diagnostic = "PROVIDER_RATE_LIMITED"
                outcome = "RATE_LIMITED"

            except ProviderUnavailable:
                diagnostic = "PROVIDER_UNAVAILABLE"
                outcome = "UNAVAILABLE"

            except ProviderNotApproved:
                attempts.append({
                    "provider_id": provider_id,
                    "attempt": attempt_number,
                    "outcome": "BLOCKED",
                    "diagnostic_code": "PROVIDER_NOT_APPROVED",
                })
                return {
                    "status": "BLOCKED",
                    "provider_id": provider_id,
                    "failover_used": provider_index > 0,
                    "diagnostic_code": "PROVIDER_NOT_APPROVED",
                    "attempts": attempts,
                    "ingestion": None,
                }

            except ProviderResponseError:
                attempts.append({
                    "provider_id": provider_id,
                    "attempt": attempt_number,
                    "outcome": "REJECTED",
                    "diagnostic_code": "INVALID_PROVIDER_RESPONSE",
                })
                return {
                    "status": "BLOCKED",
                    "provider_id": provider_id,
                    "failover_used": provider_index > 0,
                    "diagnostic_code": "INVALID_PROVIDER_RESPONSE",
                    "attempts": attempts,
                    "ingestion": None,
                }

            except ProviderError:
                attempts.append({
                    "provider_id": provider_id,
                    "attempt": attempt_number,
                    "outcome": "FAILED",
                    "diagnostic_code": "PROVIDER_ERROR",
                })
                return {
                    "status": "BLOCKED",
                    "provider_id": provider_id,
                    "failover_used": provider_index > 0,
                    "diagnostic_code": "PROVIDER_ERROR",
                    "attempts": attempts,
                    "ingestion": None,
                }

            attempts.append({
                "provider_id": provider_id,
                "attempt": attempt_number,
                "outcome": outcome,
                "diagnostic_code": diagnostic,
            })

            if attempt_number < max_attempts_per_provider:
                delay = min(
                    backoff_base_seconds * (2 ** (attempt_number - 1)),
                    backoff_cap_seconds,
                )
                if delay > 0:
                    pause(delay)

    return {
        "status": "UNAVAILABLE",
        "provider_id": None,
        "failover_used": len(providers) > 1,
        "diagnostic_code": "ALL_PROVIDERS_UNAVAILABLE",
        "attempts": attempts,
        "ingestion": None,
    }
