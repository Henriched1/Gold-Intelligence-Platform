"""Macro-calendar import, query and event-window assessment endpoints."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.data import macro_calendar_store as store
from app.engines.macro_event_risk import assess_macro_event_risk

router = APIRouter(tags=["macro-intelligence"])


class MacroEventInput(BaseModel):
    event_id: str = Field(min_length=3, max_length=128)
    title: str = Field(min_length=2, max_length=240)
    event_type: str = Field(min_length=2, max_length=80)
    scheduled_at: datetime
    currency_codes: list[str] = Field(min_length=1, max_length=10)
    impact: Literal["LOW", "MEDIUM", "HIGH"]
    source_name: str = Field(min_length=2, max_length=120)
    source_url: str = Field(min_length=8, max_length=500)
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    unit: str = Field(default="UNSPECIFIED", max_length=40)

    @field_validator("scheduled_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must include a timezone offset")
        return value

    @field_validator("currency_codes")
    @classmethod
    def currencies_valid(cls, values):
        result = sorted({value.strip().upper() for value in values})
        if any(len(value) != 3 or not value.isalpha() for value in result):
            raise ValueError("currency_codes must contain three-letter currency codes")
        return result


class MacroEventBatch(BaseModel):
    events: list[MacroEventInput] = Field(min_length=1, max_length=200)


class MacroRiskRequest(BaseModel):
    instrument: str = Field(min_length=3, max_length=20)
    as_of: datetime
    pre_event_minutes: int = Field(default=60, ge=0, le=1440)
    post_event_minutes: int = Field(default=30, ge=0, le=1440)

    @field_validator("as_of")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must include a timezone offset")
        return value


def serialise_event(event):
    data = event.model_dump()
    data["scheduled_at"] = data["scheduled_at"].astimezone(timezone.utc).isoformat()
    data["imported_at"] = datetime.now(timezone.utc).isoformat()
    return data


@router.post("/v1/macro/events/import")
def import_macro_events(request: MacroEventBatch):
    result = store.insert_macro_events([serialise_event(event) for event in request.events])
    return {
        **result,
        "source_status": "UNVERIFIED",
        "calendar_coverage_verified": False,
        "note": "Append-only import. Existing event IDs are never overwritten.",
    }


@router.get("/v1/macro/events")
def get_macro_events(
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    impact: Literal["LOW", "MEDIUM", "HIGH"] | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
):
    for label, value in (("start_at", start_at), ("end_at", end_at)):
        if value and (value.tzinfo is None or value.utcoffset() is None):
            raise HTTPException(status_code=422, detail=f"{label} must include a timezone offset")
    if start_at and end_at and start_at > end_at:
        raise HTTPException(status_code=422, detail="start_at must not exceed end_at")
    events = store.list_macro_events(
        start_at=start_at.astimezone(timezone.utc).isoformat() if start_at else None,
        end_at=end_at.astimezone(timezone.utc).isoformat() if end_at else None,
        currency=currency.upper() if currency else None,
        impact=impact,
        limit=limit,
    )
    return {"events": events, "count": len(events), "source_status": "UNVERIFIED", "calendar_coverage_verified": False}


@router.post("/v1/macro/assess")
def assess_macro_risk(request: MacroRiskRequest):
    try:
        return assess_macro_event_risk(
            request.instrument, request.as_of,
            store.list_macro_events(limit=1000),
            request.pre_event_minutes, request.post_event_minutes,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
