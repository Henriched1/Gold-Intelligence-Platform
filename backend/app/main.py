from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.core.database import initialise_database, save_forecast, get_forecasts
from app.engines.structure import analyse_structure


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialise_database()
    yield


app = FastAPI(
    title="ORACLE FX",
    description="Independent AI-powered XAUUSD market intelligence.",
    version="0.1.0",
    lifespan=lifespan
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


@app.get("/")
def root():
    return {
        "name": "ORACLE FX",
        "initial_market": "XAUUSD",
        "broker_connection": False,
        "trade_execution": False,
        "live_data_connected": False,
        "status": "FOUNDATION"
    }


@app.get("/health")
def health():
    return {"status": "healthy", "service": "oracle-fx-api"}


@app.post("/v1/analyse")
def analyse_market(request: AnalysisRequest):
    if request.instrument.upper() != "XAUUSD":
        raise HTTPException(status_code=400, detail="Initial instrument is XAUUSD.")

    candles = [c.model_dump() for c in request.candles]
    analysis = analyse_structure(candles)

    record = {
        "id": str(uuid4()),
        "instrument": "XAUUSD",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "horizon": request.horizon,
        "reference_price": candles[-1]["close"],
        "analysis": analysis,
        "status": analysis["status"]
    }

    save_forecast(record)

    return {
        **record,
        "data_source": "USER_SUPPLIED_CANDLES",
        "probability": None,
        "forecast_warning": "Preliminary structural research, not a validated forecast."
    }


@app.get("/v1/forecasts")
def forecast_history(limit: int = 50):
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="Limit must be 1–500.")
    return {"forecasts": get_forecasts(limit)}
