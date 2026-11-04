"""Controlled provider-to-store ingestion pipeline."""

from datetime import datetime, timezone

from app.data import historical_store
from app.data.provider_approval import load_approval, validate_approval
from app.data.provider_contract import (
    HistoricalDataProvider,
    ProviderRequest,
    ProviderError,
    ProviderNotApproved,
    ProviderResponseError,
    SUPPORTED_INSTRUMENTS,
    SUPPORTED_TIMEFRAMES,
)
from app.data.provider_normalizer import normalise_batch, reject_future_candles


def _validate_request(request):
    if not isinstance(request, ProviderRequest):
        raise ValueError("request must be a ProviderRequest")

    if request.instrument not in SUPPORTED_INSTRUMENTS:
        raise ValueError("unsupported canonical instrument")

    if request.timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError("unsupported canonical timeframe")

    try:
        start = datetime.fromisoformat(
            request.start.replace("Z", "+00:00")
        )
        end = datetime.fromisoformat(
            request.end.replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as error:
        raise ValueError("request dates must be ISO-8601") from error

    if (
        start.tzinfo is None
        or start.utcoffset() is None
        or end.tzinfo is None
        or end.utcoffset() is None
    ):
        raise ValueError("request dates must include a timezone")

    if start >= end:
        raise ValueError("request start must be before end")


def ingest_provider_batch(provider, request, approval_path="config/provider_approvals.json"):
    """Fetch, validate and append one provider batch.

    This function does not retry automatically. The caller can decide
    when to retry after a rate limit or temporary provider outage.
    """

    if not isinstance(provider, HistoricalDataProvider):
        raise TypeError("provider does not implement the required contract")

    _validate_request(request)

    metadata = provider.metadata
    metadata.validate(request.instrument, request.timeframe)
    approval = load_approval(approval_path)
    validate_approval(
        approval,
        request.instrument,
        request.timeframe,
        provider_id=metadata.provider_id,
    )

    try:
        observations = provider.fetch_candles(request)
    except ProviderError:
        raise
    except Exception as error:
        # Do not expose vendor response bodies, credentials or request URLs.
        raise ProviderResponseError(
            "provider fetch failed; inspect secured provider diagnostics"
        ) from error

    candles = normalise_batch(
        observations,
        request.instrument,
        request.timeframe,
    )
    reject_future_candles(candles)
    request_start = datetime.fromisoformat(
        request.start.replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    request_end = datetime.fromisoformat(
        request.end.replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    for candle in candles:
        candle_time = datetime.fromisoformat(
            candle["timestamp"].replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        if not request_start <= candle_time <= request_end:
            raise ProviderResponseError(
                "provider returned a candle outside the requested range"
            )

    result = historical_store.ingest_candles(
        instrument=request.instrument,
        timeframe=request.timeframe,
        candles=candles,
        source=metadata.provider_id,
        source_version=metadata.provider_version,
        session_convention=metadata.session_convention,
        price_component=metadata.price_component,
    )

    return {
        "status": "INGESTED",
        "instrument": request.instrument,
        "timeframe": request.timeframe,
        "provider_id": metadata.provider_id,
        "provider_version": metadata.provider_version,
        "price_component": metadata.price_component,
        "session_convention": metadata.session_convention,
        "volume_semantics": metadata.volume_semantics,
        "licensing_status": metadata.licensing_status,
        **result,
    }
