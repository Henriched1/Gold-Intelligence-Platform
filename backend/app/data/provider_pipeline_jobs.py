"""Persistent execution wrapper for explicitly requested ingestion jobs."""

from app.data.provider_contract import (
    ProviderError,
    ProviderNotApproved,
    ProviderRateLimited,
    ProviderRequest,
    ProviderResponseError,
    ProviderUnavailable,
)
from app.data.provider_pipeline import ingest_with_failover
from app.data.provider_pipeline_store import (
    create_job,
    finish_attempt,
    finish_job,
    get_job,
    mark_job_running,
    start_attempt,
)


def _attempt_failure(error):
    if isinstance(error, ProviderRateLimited):
        return "RATE_LIMITED", "PROVIDER_RATE_LIMITED"
    if isinstance(error, ProviderUnavailable):
        return "UNAVAILABLE", "PROVIDER_UNAVAILABLE"
    if isinstance(error, ProviderNotApproved):
        return "BLOCKED", "PROVIDER_NOT_APPROVED"
    if isinstance(error, ProviderResponseError):
        return "REJECTED", "INVALID_PROVIDER_RESPONSE"
    if isinstance(error, ProviderError):
        return "FAILED", "PROVIDER_ERROR"
    return "FAILED", "UNEXPECTED_ERROR"


def run_persistent_ingestion(
    idempotency_key,
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
    """Run one explicitly requested, idempotent ingestion job.

    Existing jobs are returned without replay. Provider calls happen only
    during this explicit function invocation. Raw provider exceptions and
    ingestion payloads are never written to persistent job history.
    """
    if not isinstance(request, ProviderRequest):
        raise ValueError("request must be a ProviderRequest")

    job, created = create_job(idempotency_key, providers, request)
    if not created:
        return {
            "executed": False,
            "job": job,
            "pipeline_result": None,
        }

    job_id = job["job_id"]
    mark_job_running(job_id)

    def recording_ingest(provider, data_request, approval_path):
        provider_id = provider.metadata.provider_id
        attempt_number = recording_ingest.counts.get(provider_id, 0) + 1
        recording_ingest.counts[provider_id] = attempt_number

        attempt_id = start_attempt(
            job_id, provider_id, attempt_number
        )

        try:
            result = (
                ingest_fn(provider, data_request, approval_path=approval_path)
                if ingest_fn is not None
                else None
            )
            if ingest_fn is None:
                from app.data.provider_ingestion import ingest_provider_batch
                result = ingest_provider_batch(
                    provider, data_request, approval_path=approval_path
                )
        except Exception as error:
            outcome, diagnostic = _attempt_failure(error)
            finish_attempt(
                job_id,
                provider_id,
                attempt_id,
                attempt_number,
                outcome,
                diagnostic,
            )
            raise

        finish_attempt(
            job_id,
            provider_id,
            attempt_id,
            attempt_number,
            "INGESTED",
        )
        return result

    recording_ingest.counts = {}

    try:
        result = ingest_with_failover(
            providers,
            request,
            approval_paths=approval_paths,
            max_attempts_per_provider=max_attempts_per_provider,
            backoff_base_seconds=backoff_base_seconds,
            backoff_cap_seconds=backoff_cap_seconds,
            ingest_fn=recording_ingest,
            sleep_fn=sleep_fn,
        )

        status = result["status"]
        if status not in {"INGESTED", "UNAVAILABLE", "BLOCKED"}:
            raise RuntimeError("unsupported pipeline result state")

        summary = {
            "status": status,
            "attempt_count": len(result.get("attempts", [])),
            "failover_used": bool(result.get("failover_used", False)),
        }
        if result.get("diagnostic_code"):
            summary["diagnostic_code"] = result["diagnostic_code"]
        if result.get("provider_id"):
            summary["provider_id"] = result["provider_id"]

        finished = finish_job(job_id, status, summary)
        return {
            "executed": True,
            "job": finished,
            "pipeline_result": result,
        }

    except Exception:
        # Do not persist exception text, response bodies or credentials.
        current = get_job(job_id)
        if current is not None and current["status"] == "RUNNING":
            finish_job(
                job_id,
                "FAILED",
                {"diagnostic_code": "UNEXPECTED_ERROR"},
            )
        raise
