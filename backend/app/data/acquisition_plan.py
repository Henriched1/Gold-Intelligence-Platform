"""Deterministic, bounded plans for historical candle acquisition.

This module only creates request plans. It never calls a provider and
never writes market data.
"""

import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.data.provider_contract import SUPPORTED_TIMEFRAMES

# Preserve the acquisition planner's original approved scope.
# Global instrument support does not grant historical acquisition permission.
ACQUISITION_SUPPORTED_INSTRUMENTS = frozenset({"XAUUSD"})

MAX_REQUEST_ROWS = 5000
MAX_PLAN_CHUNKS = 1000

TIMEFRAME_SECONDS = {
    "M15": 15 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
}


def _parse_aware(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 string")

    try:
        parsed = datetime.fromisoformat(
            value.strip().replace("Z", "+00:00")
        )
    except ValueError:
        raise ValueError(f"{field} must be valid ISO-8601") from None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")

    return parsed


def _iso(value):
    return value.isoformat()


def _validate_alignment(value, timeframe):
    seconds = TIMEFRAME_SECONDS[timeframe]

    if value.microsecond != 0 or int(value.timestamp()) % seconds != 0:
        raise ValueError(
            f"{timeframe} request boundaries must align to UTC candle intervals"
        )


def build_acquisition_plan(
    instrument,
    timeframe,
    start,
    end,
    max_rows=MAX_REQUEST_ROWS,
    exchange_timezone="UTC",
):
    """Build inclusive, non-overlapping request windows.

    Intraday ranges must align to UTC candle boundaries.
    D1 ranges are expanded to inclusive local calendar dates in the
    explicitly supplied exchange timezone.

    bar_slots counts time-grid positions, not guaranteed market candles.
    Weekends, holidays and provider omissions can reduce actual results.
    """

    instrument = str(instrument).upper()

    if instrument not in ACQUISITION_SUPPORTED_INSTRUMENTS:
        raise ValueError("unsupported instrument")

    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError("unsupported timeframe")

    if (
        not isinstance(max_rows, int)
        or isinstance(max_rows, bool)
        or not 1 <= max_rows <= MAX_REQUEST_ROWS
    ):
        raise ValueError(
            f"max_rows must be between 1 and {MAX_REQUEST_ROWS}"
        )

    start_value = _parse_aware(start, "start")
    end_value = _parse_aware(end, "end")

    start_utc = start_value.astimezone(timezone.utc)
    end_utc = end_value.astimezone(timezone.utc)

    if start_utc > end_utc:
        raise ValueError("start must not be after end")

    chunks = []

    if timeframe == "D1":
        if not isinstance(exchange_timezone, str) or not exchange_timezone:
            raise ValueError("D1 requires an approved exchange timezone")

        try:
            zone = ZoneInfo(exchange_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("invalid exchange timezone") from None

        first_day = start_utc.astimezone(zone).date()
        last_day = end_utc.astimezone(zone).date()

        if first_day > last_day:
            raise ValueError("daily date range is invalid")

        total_slots = (last_day - first_day).days + 1
        chunk_count = (total_slots + max_rows - 1) // max_rows

        if chunk_count > MAX_PLAN_CHUNKS:
            raise ValueError("plan exceeds the maximum chunk count")

        cursor_day = first_day

        while cursor_day <= last_day:
            chunk_last_day = min(
                cursor_day + timedelta(days=max_rows - 1),
                last_day,
            )

            chunk_start = datetime.combine(
                cursor_day, time.min, tzinfo=zone
            )
            chunk_end = datetime.combine(
                chunk_last_day, time.min, tzinfo=zone
            )

            chunks.append({
                "chunk": len(chunks) + 1,
                "start": _iso(chunk_start),
                "end": _iso(chunk_end),
                "bar_slots": (chunk_last_day - cursor_day).days + 1,
                "max_rows": max_rows,
            })

            cursor_day = chunk_last_day + timedelta(days=1)

        plan_timezone = exchange_timezone

    else:
        _validate_alignment(start_utc, timeframe)
        _validate_alignment(end_utc, timeframe)

        interval = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
        total_slots = int(
            (end_utc - start_utc).total_seconds()
            // interval.total_seconds()
        ) + 1

        chunk_count = (total_slots + max_rows - 1) // max_rows

        if chunk_count > MAX_PLAN_CHUNKS:
            raise ValueError("plan exceeds the maximum chunk count")

        cursor = start_utc
        remaining = total_slots

        while remaining:
            slots = min(remaining, max_rows)
            chunk_end = cursor + interval * (slots - 1)

            chunks.append({
                "chunk": len(chunks) + 1,
                "start": _iso(cursor),
                "end": _iso(chunk_end),
                "bar_slots": slots,
                "max_rows": max_rows,
            })

            cursor = chunk_end + interval
            remaining -= slots

        plan_timezone = "UTC"

    plan_body = {
        "instrument": instrument,
        "timeframe": timeframe,
        "requested_start_utc": _iso(start_utc),
        "requested_end_utc": _iso(end_utc),
        "planning_timezone": plan_timezone,
        "max_rows_per_request": max_rows,
        "bar_slots": total_slots,
        "chunk_count": len(chunks),
        "chunks": chunks,
        "execution_enabled": False,
        "network_requests_made": 0,
        "market_data_written": False,
        "status": "PLANNED_NOT_EXECUTED",
        "limitations": [
            "Time-grid slots are not guaranteed market candles.",
            "Session gaps are not inferred without an approved session calendar.",
            "Every chunk must pass provider licensing and execution checks.",
            "No automatic retries or gap filling are performed.",
        ],
    }

    canonical = json.dumps(
        plan_body,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    plan_body["plan_id"] = hashlib.sha256(canonical).hexdigest()

    return plan_body
