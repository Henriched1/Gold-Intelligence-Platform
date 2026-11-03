from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.core.database import (
    initialise_database,
    save_forecast,
    get_forecasts,
)
from app.engines.structure import analyse_structure
from app.engines.multi_timeframe import analyse_multi_timeframe
from app.engines.event_sequence import analyse_event_sequence


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialise_database()
    yield


app = FastAPI(
    title="ORACLE FX",
    description="Independent AI-powered XAUUSD market intelligence.",
    version="0.3.0",
    lifespan=lifespan,
)


class Candle(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0


class AnalysisRequest(BaseModel):
    instrument: str = "XAUUSD"
    horizon: str = "M15"
    candles: list[Candle] = Field(min_length=12, max_length=2000)


class MultiTimeframeRequest(BaseModel):
    instrument: str = "XAUUSD"
    d1: list[Candle] = Field(min_length=8, max_length=2000)
    h4: list[Candle] = Field(min_length=8, max_length=2000)
    h1: list[Candle] = Field(min_length=8, max_length=2000)
    m15: list[Candle] = Field(min_length=8, max_length=2000)


def check_instrument(instrument):
    if instrument.upper() != "XAUUSD":
        raise HTTPException(
            status_code=400,
            detail="Initial instrument is XAUUSD.",
        )


def prepare_timeframes(request):
    return {
        "D1": [c.model_dump() for c in request.d1],
        "H4": [c.model_dump() for c in request.h4],
        "H1": [c.model_dump() for c in request.h1],
        "M15": [c.model_dump() for c in request.m15],
    }


def save_analysis(horizon, reference_price, analysis, status):
    record = {
        "id": str(uuid4()),
        "instrument": "XAUUSD",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "horizon": horizon,
        "reference_price": reference_price,
        "analysis": analysis,
        "status": status,
    }
    save_forecast(record)
    return record


@app.get("/")
def root():
    return {
        "name": "ORACLE FX",
        "initial_market": "XAUUSD",
        "broker_connection": False,
        "trade_execution": False,
        "live_data_connected": False,
        "status": "FOUNDATION",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "oracle-fx-api",
    }


@app.post("/v1/analyse")
def analyse_market(request: AnalysisRequest):
    check_instrument(request.instrument)

    candles = [c.model_dump() for c in request.candles]
    analysis = analyse_structure(candles)

    record = save_analysis(
        request.horizon,
        candles[-1]["close"],
        analysis,
        analysis["status"],
    )

    return {
        **record,
        "data_source": "USER_SUPPLIED_CANDLES",
        "probability": None,
        "forecast_warning": (
            "Preliminary structural research, not a validated forecast."
        ),
    }


@app.post("/v1/multi-timeframe/analyse")
def analyse_multiple_timeframes(request: MultiTimeframeRequest):
    check_instrument(request.instrument)
    data = prepare_timeframes(request)

    try:
        analysis = analyse_multi_timeframe(data)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))

    record = save_analysis(
        "MULTI_TIMEFRAME",
        data["M15"][-1]["close"],
        analysis,
        analysis["alignment"],
    )

    return {
        **record,
        "data_source": "USER_SUPPLIED_MULTI_TIMEFRAME_CANDLES",
        "probability": None,
        "trade_signal": None,
        "forecast_warning": (
            "Preliminary structural research. "
            "Predictive accuracy has not been validated."
        ),
    }


@app.post("/v1/event-sequence/analyse")
def analyse_sequence(request: MultiTimeframeRequest):
    check_instrument(request.instrument)
    data = prepare_timeframes(request)

    try:
        analysis = analyse_event_sequence(data)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))

    record = save_analysis(
        "EVENT_SEQUENCE",
        data["M15"][-1]["close"],
        analysis,
        analysis["overall_status"],
    )

    return {
        **record,
        "data_source": "USER_SUPPLIED_MULTI_TIMEFRAME_CANDLES",
        "probability": None,
        "trade_signal": None,
        "forecast_warning": (
            "Sequence observations are research findings, "
            "not validated trading forecasts."
        ),
    }


@app.get("/v1/forecasts")
def forecast_history(limit: int = 50):
    if not 1 <= limit <= 500:
        raise HTTPException(
            status_code=400,
            detail="Limit must be 1–500.",
        )

    return {"forecasts": get_forecasts(limit)}
