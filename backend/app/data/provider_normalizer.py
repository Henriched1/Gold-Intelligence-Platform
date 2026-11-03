"""Validate and normalise provider observations into canonical candles."""

import math
from datetime import datetime, timezone

from app.data.provider_contract import (
    SUPPORTED_INSTRUMENTS,
    SUPPORTED_TIMEFRAMES,
    ProviderResponseError,
)


OHLC_FIELDS = ("open", "high", "low", "close")
REQUIRED_FIELDS = ("timestamp", *OHLC_FIELDS)
MAX_PROVIDER_BATCH = 100_000


def normalise_timestamp(value):
    if not isinstance(value, str):
        raise ProviderResponseError(
            "provider timestamp must be an ISO-8601 string"
        )

    try:
        parsed = datetime.fromisoformat(
            value.strip().replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ProviderResponseError(
            "provider timestamp is invalid ISO-8601"
        ) from error

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderResponseError(
            "provider timestamp must include a timezone"
        )

    return parsed.astimezone(timezone.utc).isoformat()


def _finite_number(value, field):
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ProviderResponseError(
            f"provider {field} must be numeric"
        ) from error

    if not math.isfinite(number):
        raise ProviderResponseError(
            f"provider {field} must be finite"
        )

    return number


def normalise_candle(observation):
    if not isinstance(observation, dict):
        raise ProviderResponseError(
            "each provider candle must be an object"
        )

    missing = [
        field for field in REQUIRED_FIELDS
        if field not in observation
    ]

    if missing:
        raise ProviderResponseError(
            "provider candle missing fields: " + ", ".join(missing)
        )

    candle = {
        "timestamp": normalise_timestamp(observation["timestamp"])
    }

    for field in OHLC_FIELDS:
        candle[field] = _finite_number(observation[field], field)

    if candle["high"] < max(
        candle["open"], candle["close"], candle["low"]
    ):
        raise ProviderResponseError(
            "provider candle has inconsistent high value"
        )

    if candle["low"] > min(
        candle["open"], candle["close"], candle["high"]
    ):
        raise ProviderResponseError(
            "provider candle has inconsistent low value"
        )

    volume_value = observation.get("volume")
    explicit_availability = observation.get("volume_available")

    if explicit_availability is not None and not isinstance(
        explicit_availability, bool
    ):
        raise ProviderResponseError(
            "volume_available must be true or false"
        )

    if explicit_availability is None:
        volume_available = volume_value is not None
    else:
        volume_available = explicit_availability

    if volume_value is None:
        if volume_available:
            raise ProviderResponseError(
                "volume is marked available but its value is missing"
            )
        volume = 0.0
    else:
        volume = _finite_number(volume_value, "volume")

    if volume < 0:
        raise ProviderResponseError(
            "provider volume must be non-negative"
        )

    if not volume_available and volume != 0:
        raise ProviderResponseError(
            "unavailable volume must not contain a non-zero value"
        )

    candle["volume"] = volume
    candle["volume_available"] = volume_available

    return candle


def normalise_batch(
    observations,
    instrument,
    timeframe,
    max_rows=MAX_PROVIDER_BATCH,
):
    instrument = str(instrument).upper()

    if instrument not in SUPPORTED_INSTRUMENTS:
        raise ValueError("unsupported canonical instrument")

    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError("unsupported canonical timeframe")

    if (
        not isinstance(max_rows, int)
        or isinstance(max_rows, bool)
        or not 1 <= max_rows <= MAX_PROVIDER_BATCH
    ):
        raise ValueError("max_rows must be between 1 and 100000")

    if not isinstance(observations, list) or not observations:
        raise ProviderResponseError(
            "provider returned no candle observations"
        )

    if len(observations) > max_rows:
        raise ProviderResponseError(
            "provider response exceeds the configured row limit"
        )

    candles = [
        normalise_candle(item)
        for item in observations
    ]

    timestamps = [item["timestamp"] for item in candles]

    if timestamps != sorted(timestamps):
        raise ProviderResponseError(
            "provider candles must be chronological"
        )

    if len(set(timestamps)) != len(timestamps):
        raise ProviderResponseError(
            "provider candles contain duplicate UTC timestamps"
        )

    return candles
