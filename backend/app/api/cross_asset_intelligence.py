"""Cross-asset research endpoint."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.engines.cross_asset_intelligence import analyse_cross_asset

router = APIRouter(tags=["cross-asset-intelligence"])


class Observation(BaseModel):
    timestamp: datetime
    close: float = Field(gt=0)

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone offset")
        return value


class InstrumentSeries(BaseModel):
    symbol: str = Field(min_length=3, max_length=20)
    candles: list[Observation] = Field(min_length=21, max_length=2000)

    @field_validator("candles")
    @classmethod
    def chronological_unique(cls, values):
        timestamps = [item.timestamp.astimezone(timezone.utc).isoformat() for item in values]
        if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
            raise ValueError("candles must be unique and chronological")
        return values


class CrossAssetRequest(BaseModel):
    timeframe: Literal["D1", "H4", "H1", "M15"]
    instruments: list[InstrumentSeries] = Field(min_length=2, max_length=20)


@router.post("/v1/cross-asset/analyse")
def cross_asset_analysis(request: CrossAssetRequest):
    payload = [
        {
            "symbol": item.symbol,
            "candles": [
                {"timestamp": candle.timestamp, "close": candle.close}
                for candle in item.candles
            ],
        }
        for item in request.instruments
    ]
    try:
        result = analyse_cross_asset(payload)
        result["timeframe"] = request.timeframe
        result["timeframe_validation"] = (
            "CALLER_DECLARED_NOT_INDEPENDENTLY_VALIDATED"
        )
        return result
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
