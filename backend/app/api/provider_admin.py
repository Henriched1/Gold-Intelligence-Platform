"""Authenticated, fail-closed provider administration endpoints."""

import hmac
import os
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.data import provider_onboarding as store
from app.data.provider_connection import run_provider_connection_preflight

router = APIRouter(prefix="/v1/admin", tags=["provider-admin"])

ADMIN_TOKEN_ENV = "ORACLE_FX_ADMIN_TOKEN"


def require_admin(authorization: str | None = Header(default=None)):
    configured = os.getenv(ADMIN_TOKEN_ENV, "")

    if len(configured) < 32:
        raise HTTPException(
            status_code=503,
            detail="Provider administration is disabled until a valid admin token is configured.",
        )

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Admin bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, separator, supplied = authorization.partition(" ")

    if (
        not separator
        or scheme.lower() != "bearer"
        or not supplied
        or any(character.isspace() for character in supplied)
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin authorization.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not hmac.compare_digest(
        supplied.encode("utf-8"),
        configured.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin authorization.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return True


class ProviderRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    adapter_key: str = Field(min_length=1, max_length=120)
    credential_env_var: str = Field(
        pattern=r"^[A-Z][A-Z0-9_]{1,127}$"
    )
    supported_instruments: list[str] = Field(
        min_length=1, max_length=30
    )
    historical_timeframes: list[str] = Field(
        min_length=1, max_length=4
    )
    live_timeframes: list[str] = Field(
        default_factory=list, max_length=4
    )
    notes: str = Field(default="", max_length=1000)


class LifecycleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lifecycle_state: Literal["PENDING_REVIEW", "ACTIVE", "DISABLED"]


@router.get("/providers", dependencies=[Depends(require_admin)])
def providers():
    return {"providers": store.list_providers()}


@router.post(
    "/providers",
    status_code=201,
    dependencies=[Depends(require_admin)],
)
def register_provider(request: ProviderRegistration):
    try:
        result = store.register_provider(**request.model_dump())
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="Provider ID already exists.",
        ) from None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None

    return {
        "provider": result,
        "message": "Provider registered for review. No licensing or signal approval was granted.",
    }


@router.patch(
    "/providers/{provider_id}/lifecycle",
    dependencies=[Depends(require_admin)],
)
def update_provider_lifecycle(
    provider_id: str,
    request: LifecycleUpdate,
):
    try:
        result = store.set_provider_lifecycle(
            provider_id,
            request.lifecycle_state,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Provider not found.",
        ) from None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None

    return {"provider": result}


@router.get(
    "/providers/{provider_id}/connection-tests",
    dependencies=[Depends(require_admin)],
)
def provider_connection_history(
    provider_id: str,
    limit: int = Query(default=50, ge=1, le=500),
):
    if store.get_provider(provider_id) is None:
        raise HTTPException(status_code=404, detail="Provider not found.")

    return {
        "provider_id": provider_id,
        "tests": store.list_connection_tests(provider_id, limit=limit),
        "note": "History only. This endpoint does not perform a connection test.",
    }

@router.post(
    "/providers/{provider_id}/connection-test",
    dependencies=[Depends(require_admin)],
)
def run_provider_preflight(provider_id: str):
    try:
        return run_provider_connection_preflight(provider_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Provider not found.",
        ) from None

# Phase 16 Step 3: authenticated, read-only ingestion-job observability.
from app.data import provider_pipeline_store as pipeline_store


@router.get(
    "/ingestion-jobs",
    dependencies=[Depends(require_admin)],
)
def ingestion_jobs(
    limit: int = Query(default=50, ge=1, le=500),
):
    return {
        "jobs": pipeline_store.list_jobs(limit=limit),
        "execution_enabled": False,
        "note": "Read-only job history. This endpoint does not execute ingestion.",
    }


@router.get(
    "/ingestion-jobs/{job_id}",
    dependencies=[Depends(require_admin)],
)
def ingestion_job(job_id: str):
    job = pipeline_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Ingestion job not found.")

    return {
        "job": job,
        "execution_enabled": False,
    }


@router.get(
    "/ingestion-jobs/{job_id}/events",
    dependencies=[Depends(require_admin)],
)
def ingestion_job_events(job_id: str):
    try:
        events = pipeline_store.list_job_events(job_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Ingestion job not found.",
        ) from None

    return {
        "job_id": job_id,
        "events": events,
        "history_append_only": True,
        "execution_enabled": False,
    }


# Phase 16 Step 4: authenticated, queue-only historical ingestion submission.
class HistoricalIngestionSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument: str = Field(min_length=1, max_length=20)
    timeframe: str = Field(min_length=1, max_length=10)
    start: str = Field(min_length=10, max_length=64)
    end: str = Field(min_length=10, max_length=64)
    provider_ids: list[str] = Field(min_length=1, max_length=5)


@router.post(
    "/ingestion-jobs",
    dependencies=[Depends(require_admin)],
)
def submit_ingestion_job(
    request: HistoricalIngestionSubmission,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    import re

    from app.data.provider_job_submission import (
        ProviderAdmissionError,
        submit_historical_job,
    )

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", idempotency_key):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_IDEMPOTENCY_KEY",
                "message": "Idempotency-Key must be 8–128 permitted characters.",
            },
        )

    try:
        result = submit_historical_job(
            idempotency_key=idempotency_key,
            instrument=request.instrument,
            timeframe=request.timeframe,
            start=request.start,
            end=request.end,
            provider_ids=request.provider_ids,
        )
    except ProviderAdmissionError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.as_detail(),
        ) from None

    response.status_code = 202 if result["created"] else 200
    return {
        **result,
        "execution_enabled": False,
        "provider_network_requests_enabled": False,
        "note": (
            "The job is queued for a separately authorized execution process. "
            "This endpoint does not download market data or start a worker."
        ),
    }
