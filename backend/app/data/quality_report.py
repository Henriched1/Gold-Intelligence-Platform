"""Read-only historical dataset quality and coverage analysis."""

import math
from datetime import datetime, timezone
from collections import defaultdict
from statistics import median

from app.data.historical_store import (
    get_data_coverage,
    get_historical_candles,
)

DEFAULT_ANOMALY_THRESHOLD = 8.0
MIN_ANOMALY_OBSERVATIONS = 20
MAX_REPORTED_ANOMALIES = 100
MAX_REPORTED_SOURCE_DIFFERENCES = 100
DEFAULT_SOURCE_TOLERANCE = 0.0005

OHLC_FIELDS = ("open", "high", "low", "close")


def _utc_datetime(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("as_of_utc must be a valid ISO-8601 timestamp") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of_utc must include a timezone")
    return value.astimezone(timezone.utc)


def _valid_ohlc(row):
    try:
        values = [float(row[field]) for field in OHLC_FIELDS]
    except (KeyError, TypeError, ValueError):
        return False

    if not all(math.isfinite(value) for value in values):
        return False

    open_price, high, low, close = values

    return (
        low <= high
        and low <= min(open_price, close)
        and high >= max(open_price, close)
    )


def _detect_anomalies(rows, threshold):
    grouped = defaultdict(list)

    for row in rows:
        if _valid_ohlc(row):
            grouped[row["source"]].append(row)

    observations_by_source = defaultdict(list)

    for source, candles in grouped.items():
        candles.sort(key=lambda item: item["timestamp"])

        for previous, current in zip(candles, candles[1:]):
            previous_close = float(previous["close"])

            if previous_close <= 0:
                continue

            close_return = (
                float(current["close"]) - previous_close
            ) / previous_close

            candle_range = (
                float(current["high"]) - float(current["low"])
            ) / previous_close

            observations_by_source[source].append({
                "timestamp": current["timestamp"],
                "source": source,
                "metric": "close_to_close_return",
                "value": close_return,
            })

            observations_by_source[source].append({
                "timestamp": current["timestamp"],
                "source": source,
                "metric": "relative_candle_range",
                "value": candle_range,
            })

    results = []
    status_by_metric = {}
    status_by_source = {}
    observation_count_by_source = {}

    metrics = (
        "close_to_close_return",
        "relative_candle_range",
    )

    for source, observations in observations_by_source.items():
        status_by_source[source] = {}
        observation_count_by_source[source] = {}

        for metric in metrics:
            selected = [
                item for item in observations
                if item["metric"] == metric
            ]
            values = [item["value"] for item in selected]

            observation_count_by_source[source][metric] = len(values)

            if len(values) < MIN_ANOMALY_OBSERVATIONS:
                status_by_source[source][metric] = "INSUFFICIENT_DATA"
                continue

            # Each provider receives its own statistical baseline.
            centre = median(values)
            mad = median([abs(value - centre) for value in values])

            status_by_source[source][metric] = "ANALYSED"

            for item in selected:
                deviation = abs(item["value"] - centre)

                if mad == 0:
                    is_anomaly = deviation > 0
                    robust_z = None
                else:
                    robust_z = (
                        0.67448975 * (item["value"] - centre) / mad
                    )
                    is_anomaly = abs(robust_z) > threshold

                if is_anomaly:
                    results.append({
                        **item,
                        "median": centre,
                        "mad": mad,
                        "robust_z": robust_z,
                        "reason": (
                            "DEVIATION_WITH_ZERO_MAD"
                            if mad == 0
                            else "ROBUST_Z_THRESHOLD_EXCEEDED"
                        ),
                    })

    for metric in metrics:
        source_statuses = [
            status_by_source[source][metric]
            for source in status_by_source
        ]

        status_by_metric[metric] = (
            "ANALYSED"
            if source_statuses
            and all(status == "ANALYSED" for status in source_statuses)
            else "INSUFFICIENT_DATA"
        )

    results.sort(
        key=lambda item: (
            item["timestamp"],
            item["source"],
            item["metric"],
        )
    )

    return {
        "status_by_metric": status_by_metric,
        "status_by_source": status_by_source,
        "observation_count_by_source": observation_count_by_source,
        "observation_count": sum(
            len(items) for items in observations_by_source.values()
        ),
        "anomaly_count": len(results),
        "anomalies": results[:MAX_REPORTED_ANOMALIES],
        "truncated": len(results) > MAX_REPORTED_ANOMALIES,
    }

def _compare_sources(rows, tolerance):
    by_timestamp = defaultdict(list)

    for row in rows:
        # Corrupt legacy rows remain visible in OHLC integrity results,
        # but must not crash or contaminate cross-source comparisons.
        if _valid_ohlc(row):
            by_timestamp[row["timestamp"]].append(row)

    comparisons = []
    comparison_count = 0

    for timestamp, candles in sorted(by_timestamp.items()):
        candles.sort(key=lambda item: item["source"])

        for index, first in enumerate(candles):
            for second in candles[index + 1:]:
                if first["source"] == second["source"]:
                    continue

                comparison_count += 1
                differences = {}

                for field in OHLC_FIELDS:
                    a = float(first[field])
                    b = float(second[field])
                    denominator = max(abs(a), abs(b), 1e-12)
                    relative_difference = abs(a - b) / denominator

                    if relative_difference > tolerance:
                        differences[field] = {
                            "source_a": a,
                            "source_b": b,
                            "relative_difference": relative_difference,
                        }

                if differences:
                    comparisons.append({
                        "timestamp": timestamp,
                        "source_a": first["source"],
                        "source_b": second["source"],
                        "differences": differences,
                    })

    return {
        "overlapping_source_pairs": comparison_count,
        "disagreement_count": len(comparisons),
        "disagreements": comparisons[
            :MAX_REPORTED_SOURCE_DIFFERENCES
        ],
        "truncated": (
            len(comparisons) > MAX_REPORTED_SOURCE_DIFFERENCES
        ),
        "relative_tolerance": tolerance,
    }


def generate_data_quality_report(
    instrument="XAUUSD",
    timeframe="M15",
    anomaly_threshold=DEFAULT_ANOMALY_THRESHOLD,
    source_tolerance=DEFAULT_SOURCE_TOLERANCE,
    *,
    as_of_utc=None,
):
    """Generate a read-only quality report for stored historical candles."""

    if (
        not isinstance(anomaly_threshold, (int, float))
        or isinstance(anomaly_threshold, bool)
        or not math.isfinite(anomaly_threshold)
        or anomaly_threshold <= 0
    ):
        raise ValueError("anomaly_threshold must be a positive finite number")

    if (
        not isinstance(source_tolerance, (int, float))
        or isinstance(source_tolerance, bool)
        or not math.isfinite(source_tolerance)
        or not 0 <= source_tolerance < 1
    ):
        raise ValueError("source_tolerance must be between 0 and 1")

    instrument = str(instrument).upper()

    coverage = get_data_coverage(instrument, timeframe)
    rows = get_historical_candles(
        instrument,
        timeframe,
        limit=100000,
    )

    report_time = (
        _utc_datetime(as_of_utc)
        if as_of_utc is not None
        else datetime.now(timezone.utc)
    )
    candle_times = [
        _utc_datetime(row["timestamp"])
        for row in rows
    ]
    newest_candle_time = max(candle_times) if candle_times else None
    future_observation_rows = sum(
        candle_time > report_time for candle_time in candle_times
    )
    newest_candle_age_seconds = (
        (report_time - newest_candle_time).total_seconds()
        if newest_candle_time is not None
        else None
    )

    volume_available = sum(
        row["volume_available"] == 1 for row in rows
    )
    volume_unavailable = sum(
        row["volume_available"] == 0 for row in rows
    )
    volume_unknown = sum(
        row["volume_available"] is None for row in rows
    )

    invalid_rows = [
        {
            "timestamp": row["timestamp"],
            "source": row["source"],
            "reason": "INVALID_OHLC",
        }
        for row in rows
        if not _valid_ohlc(row)
    ]

    missing_source_version = sum(
        not row.get("source_version") for row in rows
    )
    missing_session_convention = sum(
        not row.get("session_convention") for row in rows
    )

    anomalies = _detect_anomalies(
        rows,
        float(anomaly_threshold),
    )

    source_comparison = _compare_sources(
        rows,
        float(source_tolerance),
    )

    return {
        "status": (
            "NO_DATA" if not rows
            else "REVIEW_REQUIRED" if (
                invalid_rows
                or anomalies["anomaly_count"]
                or source_comparison["disagreement_count"]
                or volume_unavailable
                or volume_unknown
                or missing_source_version
                or missing_session_convention
                or any(
                    status == "INSUFFICIENT_DATA"
                    for status in anomalies["status_by_metric"].values()
                )
            )
            else "QUALITY_CHECKS_PASSED"
        ),
        "instrument": instrument,
        "timeframe": timeframe,
        "read_only": True,
        "report_generated_at_utc": report_time.isoformat(),
        "freshness_assessment": {
            "classification": "AGE_ONLY_NOT_CLASSIFIED",
            "age_basis": "CANDLE_OPEN_TIMESTAMP",
            "newest_candle_timestamp_utc": (
                newest_candle_time.isoformat()
                if newest_candle_time is not None else None
            ),
            "newest_candle_age_seconds": newest_candle_age_seconds,
            "future_observation_rows": future_observation_rows,
            "session_calendar_verified": False,
            "reason": (
                "Candle age alone cannot establish freshness without an "
                "approved session calendar and candle-close convention."
            ),
        },
        "quality_controls": {
            "market_data_verified": False,
            "signal_eligible": False,
            "automatic_stale_rejection": False,
            "reason": (
                "Independent source, licensing, session-calendar and "
                "freshness verification are not established."
            ),
        },
        "coverage": {
            **coverage,
            "analysed_candle_count": len(rows),
            "analysis_limit": 100000,
            "truncated": coverage["candle_count"] > len(rows),
        },
        "volume_quality": {
            "available": volume_available,
            "unavailable": volume_unavailable,
            "unknown_legacy": volume_unknown,
        },
        "metadata_quality": {
            "missing_source_version": missing_source_version,
            "missing_session_convention": missing_session_convention,
        },
        "ohlc_integrity": {
            "invalid_count": len(invalid_rows),
            "invalid_records": invalid_rows[:MAX_REPORTED_ANOMALIES],
            "truncated": len(invalid_rows) > MAX_REPORTED_ANOMALIES,
        },
        "source_comparison": source_comparison,
        "anomaly_detection": {
            "method": "MEDIAN_ABSOLUTE_DEVIATION",
            "threshold": float(anomaly_threshold),
            "minimum_observations": MIN_ANOMALY_OBSERVATIONS,
            **anomalies,
        },
        "limitations": [
            "Session gaps are not classified without an explicit session calendar.",
            "Anomalies are research flags, not proof of incorrect market data.",
            "Source differences are reported, not automatically resolved.",
            "Volume availability does not establish volume accuracy.",
            "No candles are modified, deleted, or automatically corrected.",
            "No trading signals or forecast probabilities are generated.",
        ],
    }
