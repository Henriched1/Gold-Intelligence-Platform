"""Manually invoked, fail-closed historical market-data ingestion worker."""

import os

from app.data import historical_store, provider_pipeline_store
from app.data import provider_job_submission
from app.data.provider_contract import (
    ProviderRateLimited,
    ProviderResponseError,
    ProviderUnavailable,
    ProviderRequest,
)


def _failure_code(error):
    """Return a sanitized diagnostic code without exposing provider details."""
    if isinstance(error, provider_job_submission.ProviderAdmissionError):
        return error.code
    if isinstance(error, ProviderRateLimited):
        return "PROVIDER_RATE_LIMITED"
    if isinstance(error, ProviderUnavailable):
        return "PROVIDER_UNAVAILABLE"
    if isinstance(error, ProviderResponseError):
        return "PROVIDER_RESPONSE_REJECTED"
    if isinstance(error, ValueError):
        return "REQUEST_OR_DATA_VALIDATION_FAILED"
    return "PROVIDER_ATTEMPT_FAILED"


def execute_ingestion_job(job_id, environ=None):
    """Execute one queued historical-data job. Never submits trading orders."""

    environment = os.environ if environ is None else environ
    job = provider_pipeline_store.get_job(job_id)

    if job is None:
        raise KeyError("ingestion job not found")

    if job["status"] != "QUEUED":
        raise provider_pipeline_store.JobNotRunnable(
            "only queued ingestion jobs can be executed"
        )

    running_job = provider_pipeline_store.mark_job_running(job_id)

    request = ProviderRequest(
        instrument=running_job["instrument"],
        timeframe=running_job["timeframe"],
        start=running_job["request_start"],
        end=running_job["request_end"],
    )

    failures = []
    attempts = 0
    successful_provider = None
    stored = None

    for provider_id in running_job["provider_ids"]:
        attempts += 1
        attempt_id = provider_pipeline_store.start_attempt(
            job_id, provider_id, attempts
        )

        try:
            # Revalidate lifecycle, credentials, adapter scope and written
            # licensing approval immediately before fetching.
            adapter = provider_job_submission._admit_provider(
                provider_id,
                request.instrument,
                request.timeframe,
                environment,
            )

            candles = adapter.fetch_candles(request)

            if not isinstance(candles, list) or not candles:
                raise ProviderResponseError(
                    "provider returned no historical candles"
                )

            metadata = adapter.metadata

            stored = historical_store.ingest_candles(
                instrument=request.instrument,
                timeframe=request.timeframe,
                candles=candles,
                source=metadata.provider_id,
                source_version=metadata.provider_version,
                session_convention=metadata.session_convention,
                price_component=metadata.price_component,
            )

            provider_pipeline_store.finish_attempt(
                job_id,
                provider_id,
                attempt_id,
                attempts,
                "INGESTED",
            )
            successful_provider = provider_id
            break

        except Exception as error:
            code = _failure_code(error)

            if isinstance(
                error,
                provider_job_submission.ProviderAdmissionError,
            ):
                outcome = "BLOCKED"
            elif isinstance(error, ProviderRateLimited):
                outcome = "RATE_LIMITED"
            elif isinstance(error, ProviderUnavailable):
                outcome = "UNAVAILABLE"
            elif isinstance(error, ProviderResponseError):
                outcome = "REJECTED"
            else:
                outcome = "FAILED"

            provider_pipeline_store.finish_attempt(
                job_id,
                provider_id,
                attempt_id,
                attempts,
                outcome,
                diagnostic_code=code,
            )
            failures.append({
                "provider_id": provider_id,
                "outcome": outcome,
                "diagnostic_code": code,
            })

    if successful_provider is not None:
        status = "INGESTED"
        summary = {
            "status": status,
            "attempt_count": attempts,
            "failover_used": attempts > 1,
            "provider_id": successful_provider,
        }
        if stored:
            summary["diagnostic_code"] = "HISTORICAL_CANDLES_STORED"
    else:
        outcomes = {item["outcome"] for item in failures}

        if outcomes == {"BLOCKED"}:
            status = "BLOCKED"
        elif "UNAVAILABLE" in outcomes or "RATE_LIMITED" in outcomes:
            status = "UNAVAILABLE"
        elif "BLOCKED" in outcomes and outcomes.issubset(
            {"BLOCKED", "FAILED"}
        ):
            status = "BLOCKED"
        else:
            status = "FAILED"

        summary = {
            "status": status,
            "attempt_count": attempts,
            "failover_used": attempts > 1,
            "diagnostic_code": (
                failures[-1]["diagnostic_code"]
                if failures else "NO_PROVIDER_ATTEMPTED"
            ),
        }

    finished = provider_pipeline_store.finish_job(
        job_id,
        status,
        summary,
    )

    return {
        "job": finished,
        "stored": stored,
        "provider_failures": failures,
        "worker_invoked": True,
        "historical_data_ingested": successful_provider is not None,
        "trade_execution_enabled": False,
        "live_trading_enabled": False,
        "orders_submitted": False,
    }
