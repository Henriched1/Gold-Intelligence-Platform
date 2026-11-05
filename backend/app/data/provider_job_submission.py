"""Fail-closed admission of explicit historical ingestion jobs.

This module validates local provider configuration and written licensing
approval. It creates QUEUED jobs only; it never performs provider network I/O.
"""

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from app.data import provider_approval
from app.data import provider_onboarding
from app.data import provider_pipeline_store
from app.data import provider_registry
from app.data.provider_contract import (
    ProviderNotApproved,
    ProviderRequest,
    SUPPORTED_INSTRUMENTS,
    SUPPORTED_TIMEFRAMES,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_APPROVAL_PATH = PROJECT_ROOT / "config" / "provider_approvals.json"
APPROVALS_DIR = PROJECT_ROOT / "config" / "provider_approvals"

TIMEFRAME_SECONDS = {
    "M15": 15 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
    "D1": 24 * 60 * 60,
}
MAX_PROVIDER_COUNT = 5
MAX_CANDLES_PER_REQUEST = 5000
PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class ProviderAdmissionError(Exception):
    """A safe, machine-readable reason that a job cannot be queued."""

    def __init__(self, code, message, status_code=409, provider_id=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.provider_id = provider_id

    def as_detail(self):
        detail = {
            "code": self.code,
            "message": self.message,
        }
        if self.provider_id is not None:
            detail["provider_id"] = self.provider_id
        return detail


def _parse_aware_datetime(value, field):
    if not isinstance(value, str):
        raise ProviderAdmissionError(
            "INVALID_REQUEST_DATE",
            f"{field} must be ISO-8601 text with a timezone.",
            422,
        )
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ProviderAdmissionError(
            "INVALID_REQUEST_DATE",
            f"{field} must be a valid ISO-8601 date.",
            422,
        ) from None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderAdmissionError(
            "TIMEZONE_REQUIRED",
            f"{field} must include a timezone.",
            422,
        )
    return parsed.astimezone(timezone.utc)


def _approval_path(provider_id):
    if not isinstance(provider_id, str) or not PROVIDER_ID_RE.fullmatch(provider_id):
        raise ProviderAdmissionError(
            "INVALID_PROVIDER_ID",
            "Provider ID is invalid.",
            422,
            provider_id,
        )

    # Preserve the existing Twelve Data approval-file location.
    if provider_id == "twelve_data":
        return DEFAULT_APPROVAL_PATH

    # Other providers use one isolated approval file per provider.
    return APPROVALS_DIR / f"{provider_id}.json"


def _admit_provider(provider_id, instrument, timeframe, environment):
    provider = provider_onboarding.get_provider(provider_id)
    if provider is None:
        raise ProviderAdmissionError(
            "PROVIDER_NOT_REGISTERED",
            "The requested provider is not registered.",
            404,
            provider_id,
        )

    lifecycle_state = provider.get("lifecycle_state")

    if lifecycle_state == "DISABLED":
        raise ProviderAdmissionError(
            "PROVIDER_DISABLED",
            "The requested provider is disabled.",
            409,
            provider_id,
        )

    if lifecycle_state != "ACTIVE":
        raise ProviderAdmissionError(
            "PROVIDER_NOT_ACTIVE",
            "The provider must be explicitly activated before ingestion submission.",
            409,
            provider_id,
        )

    if instrument not in provider["supported_instruments"]:
        raise ProviderAdmissionError(
            "INSTRUMENT_SCOPE_UNSUPPORTED",
            "The provider is not registered for this instrument.",
            409,
            provider_id,
        )

    if timeframe not in provider["historical_timeframes"]:
        raise ProviderAdmissionError(
            "TIMEFRAME_SCOPE_UNSUPPORTED",
            "The provider is not registered for this historical timeframe.",
            409,
            provider_id,
        )

    credential_name = provider["credential_env_var"]
    credential = environment.get(credential_name)
    if not isinstance(credential, str) or not credential.strip():
        raise ProviderAdmissionError(
            "CREDENTIAL_MISSING",
            "The provider credential is not configured in the server environment.",
            409,
            provider_id,
        )

    provider_registry.ensure_default_providers()
    factory = provider_registry.get_registered_provider_factories().get(
        provider_id
    )
    if factory is None:
        raise ProviderAdmissionError(
            "ADAPTER_UNAVAILABLE",
            "No local adapter is registered for this provider.",
            409,
            provider_id,
        )

    try:
        adapter = factory()
    except Exception:
        raise ProviderAdmissionError(
            "ADAPTER_INITIALIZATION_FAILED",
            "The local provider adapter could not be initialized.",
            409,
            provider_id,
        ) from None

    if getattr(adapter, "credentials_configured", False) is not True:
        raise ProviderAdmissionError(
            "ADAPTER_CREDENTIAL_MISSING",
            "The adapter did not confirm that its credential is configured.",
            409,
            provider_id,
        )

    metadata = getattr(adapter, "metadata", None)
    if metadata is None or getattr(metadata, "provider_id", None) != provider_id:
        raise ProviderAdmissionError(
            "ADAPTER_PROVIDER_MISMATCH",
            "The adapter identity does not match the registered provider.",
            409,
            provider_id,
        )

    try:
        metadata.validate(instrument, timeframe)
    except ProviderNotApproved:
        raise ProviderAdmissionError(
            "ADAPTER_LICENSE_NOT_APPROVED",
            "The adapter does not declare approved historical licensing scope.",
            409,
            provider_id,
        ) from None
    except (AttributeError, TypeError, ValueError):
        raise ProviderAdmissionError(
            "ADAPTER_SCOPE_UNSUPPORTED",
            "The adapter does not support the requested market-data scope.",
            409,
            provider_id,
        ) from None

    try:
        approval_record = provider_approval.load_approval(
            _approval_path(provider_id)
        )
        provider_approval.validate_approval(
            approval_record,
            instrument,
            timeframe,
            provider_id=provider_id,
        )
    except (ProviderNotApproved, OSError, ValueError, TypeError):
        raise ProviderAdmissionError(
            "WRITTEN_LICENSE_APPROVAL_REQUIRED",
            "A current written approval covering this provider, instrument, and timeframe is required.",
            409,
            provider_id,
        ) from None

    return adapter


def submit_historical_job(
    *,
    idempotency_key,
    instrument,
    timeframe,
    start,
    end,
    provider_ids,
    environ=None,
):
    """Validate and persist a QUEUED job without executing provider calls."""

    if instrument not in SUPPORTED_INSTRUMENTS:
        raise ProviderAdmissionError(
            "UNSUPPORTED_INSTRUMENT",
            "Instrument is not in the enabled ORACLE FX universe.",
            422,
        )

    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ProviderAdmissionError(
            "UNSUPPORTED_TIMEFRAME",
            "Timeframe is not supported.",
            422,
        )

    if not isinstance(provider_ids, list) or not 1 <= len(provider_ids) <= MAX_PROVIDER_COUNT:
        raise ProviderAdmissionError(
            "INVALID_PROVIDER_LIST",
            "Select between one and five registered providers.",
            422,
        )

    if any(
        not isinstance(item, str) or not PROVIDER_ID_RE.fullmatch(item)
        for item in provider_ids
    ):
        raise ProviderAdmissionError(
            "INVALID_PROVIDER_ID",
            "Every provider ID must be valid.",
            422,
        )

    if len(set(provider_ids)) != len(provider_ids):
        raise ProviderAdmissionError(
            "DUPLICATE_PROVIDER",
            "A provider may appear only once in a submission.",
            422,
        )

    start_utc = _parse_aware_datetime(start, "start")
    end_utc = _parse_aware_datetime(end, "end")
    now = datetime.now(timezone.utc)

    if start_utc >= end_utc:
        raise ProviderAdmissionError(
            "INVALID_DATE_RANGE",
            "The start date must be earlier than the end date.",
            422,
        )

    if end_utc > now:
        raise ProviderAdmissionError(
            "FUTURE_DATA_NOT_ALLOWED",
            "Historical ingestion cannot request future market data.",
            422,
        )

    requested_seconds = (end_utc - start_utc).total_seconds()
    maximum_seconds = (
        TIMEFRAME_SECONDS[timeframe] * MAX_CANDLES_PER_REQUEST
    )
    if requested_seconds > maximum_seconds:
        raise ProviderAdmissionError(
            "REQUEST_RANGE_TOO_LARGE",
            "The requested range exceeds the 5,000-candle safety limit.",
            422,
        )

    request = ProviderRequest(
        instrument=instrument,
        timeframe=timeframe,
        start=start_utc.isoformat(),
        end=end_utc.isoformat(),
    )

    # Resolve an existing idempotent retry before provider re-admission.
    # This is read-only; it cannot create or execute a new ingestion job.
    try:
        existing_job = provider_pipeline_store.find_existing_job(
            idempotency_key,
            provider_ids,
            request,
        )
    except provider_pipeline_store.IdempotencyConflict:
        raise ProviderAdmissionError(
            "IDEMPOTENCY_CONFLICT",
            "This idempotency key was already used for a different request.",
            409,
        ) from None
    except ValueError:
        raise ProviderAdmissionError(
            "INVALID_JOB_REQUEST",
            "The ingestion job failed local request validation.",
            422,
        ) from None

    if existing_job is not None:
        return {
            "job": existing_job,
            "created": False,
            "submission_status": "ALREADY_EXISTS",
            "provider_calls_performed": False,
            "execution_enabled": False,
            "signal_eligible": False,
        }

    environment = os.environ if environ is None else environ
    adapters = [
        _admit_provider(
            provider_id,
            instrument,
            timeframe,
            environment,
        )
        for provider_id in provider_ids
    ]

    try:
        job, created = provider_pipeline_store.create_job(
            idempotency_key,
            adapters,
            request,
        )
    except provider_pipeline_store.IdempotencyConflict:
        raise ProviderAdmissionError(
            "IDEMPOTENCY_CONFLICT",
            "This idempotency key was already used for a different request.",
            409,
        ) from None
    except ValueError:
        raise ProviderAdmissionError(
            "INVALID_JOB_REQUEST",
            "The ingestion job failed local request validation.",
            422,
        ) from None

    return {
        "job": job,
        "created": created,
        "submission_status": "QUEUED" if created else "ALREADY_EXISTS",
        "provider_calls_performed": False,
        "execution_enabled": False,
        "signal_eligible": False,
    }
