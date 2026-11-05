"""Global instrument coverage and research-readiness assessment.

Read-only: this endpoint does not fetch market data, emit alerts,
or enable trading. Historical coverage is not live-feed verification.
"""
from fastapi import APIRouter, HTTPException, Query

from app.core.universe import list_instruments
from app.data import historical_store, provider_approval, provider_onboarding
from app.data import provider_job_submission
from app.data.provider_contract import ProviderNotApproved

router = APIRouter(tags=["global-market-coverage"])

TIMEFRAMES = ("D1", "H4", "H1", "M15")
MIN_CANDLES = 8


def _approval_check(source, instrument, timeframe):
    provider = provider_onboarding.get_provider(source)
    if provider is None:
        return "PROVIDER_NOT_REGISTERED"
    if provider.get("lifecycle_state") != "ACTIVE":
        return "PROVIDER_NOT_ACTIVE"
    if instrument not in provider.get("supported_instruments", []):
        return "INSTRUMENT_OUTSIDE_PROVIDER_SCOPE"
    if timeframe not in provider.get("historical_timeframes", []):
        return "TIMEFRAME_OUTSIDE_PROVIDER_SCOPE"

    try:
        path = provider_job_submission._approval_path(source)
        approval = provider_approval.load_approval(path)
        provider_approval.validate_approval(
            approval, instrument, timeframe, provider_id=source
        )
    except (
        ProviderNotApproved,
        provider_job_submission.ProviderAdmissionError,
        OSError,
        TypeError,
        ValueError,
    ):
        return "CURRENT_WRITTEN_DATA_LICENCE_REQUIRED"

    return "APPROVED"


@router.get("/v1/global/coverage")
def global_market_coverage(
    source: str = Query(..., min_length=2, max_length=64),
):
    """Report historical coverage and licensing readiness for the universe."""
    provider = provider_onboarding.get_provider(source)
    if provider is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROVIDER_NOT_REGISTERED"},
        )

    results = []
    totals = {
        "instruments": 0,
        "research_ready": 0,
        "blocked": 0,
        "insufficient_history": 0,
    }

    for instrument in list_instruments():
        if not instrument["enabled"]:
            continue

        symbol = instrument["symbol"]
        timeframe_results = {}
        all_ready = True

        for timeframe in TIMEFRAMES:
            approval_status = _approval_check(
                source, symbol, timeframe
            )

            count = 0
            latest_timestamp = None
            status = approval_status

            if approval_status == "APPROVED":
                try:
                    rows = historical_store.get_historical_candles(
                        symbol,
                        timeframe,
                        source=source,
                        limit=2000,
                        latest=True,
                    )
                    count = len(rows)
                    latest_timestamp = (
                        rows[-1]["timestamp"] if rows else None
                    )
                    status = (
                        "HISTORY_AVAILABLE"
                        if count >= MIN_CANDLES
                        else "INSUFFICIENT_STORED_HISTORY"
                    )
                except (ValueError, OSError):
                    status = "HISTORY_LOOKUP_FAILED"

            ready = status == "HISTORY_AVAILABLE"
            if not ready:
                all_ready = False

            timeframe_results[timeframe] = {
                "status": status,
                "candle_count": count,
                "minimum_required": MIN_CANDLES,
                "latest_stored_timestamp": latest_timestamp,
                "licensing_approved": approval_status == "APPROVED",
            }

        totals["instruments"] += 1
        if all_ready:
            totals["research_ready"] += 1
            instrument_status = "READY_FOR_STRUCTURAL_RESEARCH"
        else:
            totals["blocked"] += 1
            instrument_status = "NOT_READY"

            if any(
                item["status"] == "INSUFFICIENT_STORED_HISTORY"
                for item in timeframe_results.values()
            ):
                totals["insufficient_history"] += 1

        results.append({
            "instrument": symbol,
            "asset_class": instrument["asset_class"],
            "status": instrument_status,
            "timeframes": timeframe_results,
        })

    return {
        "source": source,
        "provider_lifecycle": provider["lifecycle_state"],
        "universe_size": totals["instruments"],
        "totals": totals,
        "instruments": results,
        "coverage_type": "STORED_HISTORICAL_DATA",
        "freshness_verified": False,
        "live_data_connected": False,
        "session_calendar_verified": False,
        "macro_calendar_coverage_verified": False,
        "signal_eligible": False,
        "alerts_emitted": False,
        "execution_enabled": False,
        "warning": (
            "Readiness only. Stored history does not establish current "
            "market freshness, live connectivity, session validity, "
            "spread validity or confirmed-entry eligibility."
        ),
    }
