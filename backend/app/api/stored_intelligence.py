"""Research-only multi-timeframe analysis from approved stored history."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from app.core.database import save_forecast
from app.data import historical_store, provider_approval, provider_onboarding
from app.data import provider_job_submission
from app.data.provider_contract import ProviderNotApproved
from app.engines.scenario_orchestrator import analyse_instrument_scenarios

router = APIRouter(tags=["stored-market-intelligence"])
TIMEFRAMES = ("D1", "H4", "H1", "M15")
MIN_CANDLES = 8


def _approval_for_analysis(provider_id, instrument):
    provider = provider_onboarding.get_provider(provider_id)
    if provider is None:
        raise HTTPException(404, detail={"code": "PROVIDER_NOT_REGISTERED"})

    if provider.get("lifecycle_state") != "ACTIVE":
        raise HTTPException(409, detail={"code": "PROVIDER_NOT_ACTIVE"})

    if instrument not in provider.get("supported_instruments", []):
        raise HTTPException(409, detail={"code": "INSTRUMENT_OUTSIDE_PROVIDER_SCOPE"})

    if any(
        timeframe not in provider.get("historical_timeframes", [])
        for timeframe in TIMEFRAMES
    ):
        raise HTTPException(409, detail={"code": "TIMEFRAME_OUTSIDE_PROVIDER_SCOPE"})

    try:
        approval_path = provider_job_submission._approval_path(provider_id)
        approval = provider_approval.load_approval(approval_path)
        for timeframe in TIMEFRAMES:
            provider_approval.validate_approval(
                approval, instrument, timeframe, provider_id=provider_id
            )
    except (
        ProviderNotApproved,
        provider_job_submission.ProviderAdmissionError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            409,
            detail={"code": "CURRENT_WRITTEN_DATA_LICENCE_REQUIRED"},
        ) from None


@router.get("/v1/global/stored/analyse")
def analyse_stored_market(
    instrument: str = Query(default="XAUUSD", min_length=3, max_length=20),
    source: str = Query(..., min_length=2, max_length=64),
    candles_per_timeframe: int = Query(default=200, ge=8, le=2000),
):
    """Analyse stored D1/H4/H1/M15 history; never issue a trade signal."""
    symbol = instrument.strip().upper()
    _approval_for_analysis(source, symbol)

    candles_by_timeframe = {}
    coverage = {}
    all_rows = []

    try:
        for timeframe in TIMEFRAMES:
            rows = historical_store.get_historical_candles(
                symbol,
                timeframe,
                source=source,
                limit=candles_per_timeframe,
                latest=True,
            )
            coverage[timeframe] = {
                "candle_count": len(rows),
                "first_timestamp": rows[0]["timestamp"] if rows else None,
                "last_timestamp": rows[-1]["timestamp"] if rows else None,
                "source_versions": sorted({row["source_version"] for row in rows}),
                "session_conventions": sorted({row["session_convention"] for row in rows}),
                "price_components": sorted({row["price_component"] for row in rows}),
            }
            candles_by_timeframe[timeframe] = [
                {
                    "timestamp": row["timestamp"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"],
                }
                for row in rows
            ]
            all_rows.extend(rows)
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from None

    missing = {
        timeframe: coverage[timeframe]["candle_count"]
        for timeframe in TIMEFRAMES
        if coverage[timeframe]["candle_count"] < MIN_CANDLES
    }
    if missing:
        raise HTTPException(
            409,
            detail={
                "code": "INSUFFICIENT_STORED_HISTORY",
                "minimum_candles_per_timeframe": MIN_CANDLES,
                "available_candles": missing,
                "required_timeframes": list(TIMEFRAMES),
            },
        )

    now = datetime.now(timezone.utc)
    future_count = 0
    for row in all_rows:
        try:
            stamp = datetime.fromisoformat(
                row["timestamp"].replace("Z", "+00:00")
            )
        except (AttributeError, TypeError, ValueError):
            raise HTTPException(422, detail={"code": "INVALID_STORED_TIMESTAMP"}) from None
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise HTTPException(
                422,
                detail={"code": "TIMEZONE_MISSING_FROM_STORED_TIMESTAMP"},
            )
        if stamp.astimezone(timezone.utc) > now:
            future_count += 1

    if future_count:
        raise HTTPException(
            409,
            detail={
                "code": "FUTURE_OBSERVATIONS_PRESENT",
                "count": future_count,
            },
        )

    try:
        analysis = analyse_instrument_scenarios(symbol, candles_by_timeframe)
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from None

    record = {
        "id": str(uuid4()),
        "instrument": symbol,
        "created_at": now.isoformat(),
        "horizon": "STORED_GLOBAL_MULTI_TIMEFRAME",
        "reference_price": float(candles_by_timeframe["M15"][-1]["close"]),
        "analysis": analysis,
        "status": "RESEARCH_ONLY_WAIT",
    }
    save_forecast(record)

    return {
        **record,
        "data_source": "PERSISTED_PROVIDER_HISTORICAL_CANDLES",
        "provider_id": source,
        "provider_approval_validated": True,
        "provider_approval_checked_at": now.isoformat(),
        "coverage": coverage,
        "data_provenance": {
            "source": source,
            "stored_history_used": True,
            "licensing_approved_at_request": True,
            "market_data_independently_verified": False,
            "freshness_verified": False,
            "session_calendar_verified": False,
            "spread_verified": False,
            "macro_calendar_coverage_verified": False,
            "signal_eligible": False,
        },
        "probability": None,
        "trade_signal": None,
        "alerts_emitted": False,
        "execution_enabled": False,
        "live_data_connected": False,
        "warning": (
            "Stored-data structural research only. Current written "
            "historical and derived-display rights were checked, but "
            "freshness, independent price verification, session calendar, "
            "spread and event-risk evidence remain unverified. "
            "No confirmed entry or trading signal is issued."
        ),
    }
