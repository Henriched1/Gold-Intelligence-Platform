"""Global instrument universe and research-only analysis API."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.database import save_forecast
from app.core.universe import INSTRUMENTS, list_instruments
from app.engines.scenario_orchestrator import analyse_instrument_scenarios

router = APIRouter(tags=["global-intelligence"])

ASSET_CLASSES = tuple(sorted({
    item.asset_class for item in INSTRUMENTS
}))


class GlobalCandle(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0


class GlobalAnalysisRequest(BaseModel):
    instrument: str = "XAUUSD"
    d1: list[GlobalCandle] = Field(min_length=8, max_length=2000)
    h4: list[GlobalCandle] = Field(min_length=8, max_length=2000)
    h1: list[GlobalCandle] = Field(min_length=8, max_length=2000)
    m15: list[GlobalCandle] = Field(min_length=8, max_length=2000)


@router.get("/v1/universe")
def market_universe(asset_class: str | None = None):
    if asset_class:
        wanted = asset_class.strip().upper()
        if wanted not in ASSET_CLASSES:
            raise HTTPException(
                status_code=422,
                detail="Unsupported asset class. Allowed: "
                + ", ".join(ASSET_CLASSES),
            )
    else:
        wanted = None

    instruments = list_instruments(wanted)

    return {
        "instruments": instruments,
        "count": len(instruments),
        "asset_classes": list(ASSET_CLASSES),
        "live_data_available": False,
        "provider_coverage_verified": False,
        "execution_enabled": False,
    }


@router.post("/v1/global/analyse")
def analyse_global_market(request: GlobalAnalysisRequest):
    candles = {
        "D1": [item.model_dump() for item in request.d1],
        "H4": [item.model_dump() for item in request.h4],
        "H1": [item.model_dump() for item in request.h1],
        "M15": [item.model_dump() for item in request.m15],
    }

    try:
        analysis = analyse_instrument_scenarios(
            request.instrument,
            candles,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    record = {
        "id": str(uuid4()),
        "instrument": analysis["instrument"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "horizon": "GLOBAL_MULTI_TIMEFRAME",
        "reference_price": float(candles["M15"][-1]["close"]),
        "analysis": analysis,
        "status": "RESEARCH_ONLY_WAIT",
    }

    save_forecast(record)

    return {
        **record,
        "data_source": "USER_SUPPLIED_CANDLES",
        "probability": None,
        "trade_signal": None,
        "alerts_emitted": False,
        "execution_enabled": False,
    }
