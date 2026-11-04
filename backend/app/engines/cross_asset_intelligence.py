"""Descriptive cross-asset analysis of supplied, unverified observations."""

import math
from datetime import timezone
from statistics import fmean, pstdev

from app.core.universe import get_instrument


MIN_CANDLES = 21
MAX_CANDLES = 2000
MIN_ALIGNED_RETURNS = 20


def _pearson(left, right):
    if len(left) != len(right) or len(left) < MIN_ALIGNED_RETURNS:
        return None
    mean_left = fmean(left)
    mean_right = fmean(right)
    centred_left = [value - mean_left for value in left]
    centred_right = [value - mean_right for value in right]
    sum_left = math.fsum(value * value for value in centred_left)
    sum_right = math.fsum(value * value for value in centred_right)
    if sum_left <= 0 or sum_right <= 0:
        return None
    covariance = math.fsum(
        a * b for a, b in zip(centred_left, centred_right)
    )
    return max(-1.0, min(1.0, covariance / math.sqrt(sum_left * sum_right)))


def _series_returns(series):
    symbol = series["symbol"].strip().upper()
    instrument = get_instrument(symbol)
    if instrument is None or not instrument.enabled:
        raise ValueError(f"{symbol} is not in the canonical market universe")

    candles = series["candles"]
    if not MIN_CANDLES <= len(candles) <= MAX_CANDLES:
        raise ValueError(
            f"{symbol} must contain {MIN_CANDLES}–{MAX_CANDLES} observations"
        )

    timestamps = []
    closes = []
    for candle in candles:
        timestamp = candle["timestamp"]
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError(f"{symbol} timestamps must include a timezone")
        close = float(candle["close"])
        if not math.isfinite(close) or close <= 0:
            raise ValueError(f"{symbol} close prices must be finite and positive")
        timestamps.append(timestamp.astimezone(timezone.utc).isoformat())
        closes.append(close)

    if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
        raise ValueError(f"{symbol} timestamps must be unique and chronological")

    returns = {}
    for index in range(1, len(closes)):
        # A return is identified by both endpoints. Matching only the
        # ending timestamp could compare unequal-duration price moves.
        interval = (timestamps[index - 1], timestamps[index])
        returns[interval] = math.log(closes[index] / closes[index - 1])

    net_change = closes[-1] / closes[0] - 1.0
    values = list(returns.values())

    return {
        "symbol": symbol,
        "first_timestamp": timestamps[0],
        "last_timestamp": timestamps[-1],
        "observations": len(closes),
        "return_observations": len(values),
        "last_close": closes[-1],
        "net_change_fraction": net_change,
        "descriptive_trend": (
            "POSITIVE_NET_CHANGE" if net_change > 0
            else "NEGATIVE_NET_CHANGE" if net_change < 0
            else "UNCHANGED"
        ),
        "return_volatility": pstdev(values) if len(values) > 1 else None,
        "_returns": returns,
    }


def analyse_cross_asset(series):
    if not isinstance(series, list) or not 2 <= len(series) <= 20:
        raise ValueError("supply between 2 and 20 instrument series")

    summaries = [_series_returns(item) for item in series]
    symbols = [item["symbol"] for item in summaries]
    if len(set(symbols)) != len(symbols):
        raise ValueError("instrument symbols must be unique")

    correlations = []
    for index, first in enumerate(summaries):
        for second in summaries[index + 1:]:
            common = sorted(
                set(first["_returns"]).intersection(second["_returns"])
            )
            left = [first["_returns"][key] for key in common]
            right = [second["_returns"][key] for key in common]
            value = _pearson(left, right)
            correlations.append({
                "instrument_a": first["symbol"],
                "instrument_b": second["symbol"],
                "aligned_return_observations": len(common),
                "pearson_correlation": value,
                "status": (
                    "ANALYSED" if value is not None
                    else "INSUFFICIENT_OR_CONSTANT_ALIGNED_RETURNS"
                ),
            })

    for item in summaries:
        item.pop("_returns", None)

    return {
        "status": "DESCRIPTIVE_CROSS_ASSET_RESEARCH",
        "time_alignment": "EXACT_UTC_TIMESTAMP_MATCH",
        "source_status": "USER_SUPPLIED_UNVERIFIED",
        "instrument_summaries": summaries,
        "pairwise_correlations": correlations,
        "freshness_status": "NOT_CLASSIFIED_SESSION_CALENDAR_REQUIRED",
        "live_data_connected": False,
        "directional_bias": None,
        "probability": None,
        "trade_signal": None,
        "alerts_emitted": False,
        "execution_enabled": False,
        "limitations": [
            "Input observations are user-supplied and are not provider-verified.",
            "Correlation is descriptive and does not establish causation.",
            "Correlations compare returns with identical UTC start and end timestamps.",
            "Freshness and session gaps require instrument-specific calendars.",
            "This analysis does not create directional forecasts or trading signals.",
        ],
    }
