"""Validated CSV ingestion for immutable historical market candles."""

import csv
import math
from datetime import datetime, timezone
from pathlib import Path

from app.data.historical_store import ingest_candles

REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close")
MAX_IMPORT_ROWS = 100_000
MAX_REPORTED_ERRORS = 100


def _parse_timestamp(value):
    if not value:
        raise ValueError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be valid ISO-8601") from error

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")

    return parsed.astimezone(timezone.utc).isoformat()


def _number(value, field, default=None):
    if value is None or value.strip() == "":
        if default is not None:
            return default
        raise ValueError(f"{field} is required")

    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error

    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")

    return result


def _parse_row(row):
    candle = {
        "timestamp": _parse_timestamp(row.get("timestamp", "")),
        "open": _number(row.get("open"), "open"),
        "high": _number(row.get("high"), "high"),
        "low": _number(row.get("low"), "low"),
        "close": _number(row.get("close"), "close"),
        "volume": _number(row.get("volume"), "volume", 0.0),
        "volume_available": (
            row.get("volume") is not None
            and row.get("volume").strip() != ""
        ),
    }

    if candle["volume"] < 0:
        raise ValueError("volume must be non-negative")

    if candle["high"] < max(
        candle["open"], candle["close"], candle["low"]
    ):
        raise ValueError("high is inconsistent with OHLC values")

    if candle["low"] > min(
        candle["open"], candle["close"], candle["high"]
    ):
        raise ValueError("low is inconsistent with OHLC values")

    return candle


def import_csv(
    file_path,
    instrument,
    timeframe,
    source,
    source_version="unspecified",
    session_convention="unspecified",
):
    """Validate the entire CSV before writing any candles."""

    path = Path(file_path)

    if not path.is_file():
        raise FileNotFoundError(f"CSV file not found: {path}")

    candles = []
    errors = []
    rows_read = 0

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)

            if not reader.fieldnames:
                raise ValueError("CSV is empty or has no header")

            headers = [name.strip() for name in reader.fieldnames]

            if len(headers) != len(set(headers)):
                raise ValueError("CSV contains duplicate column names")

            missing = [
                name for name in REQUIRED_COLUMNS if name not in headers
            ]

            if missing:
                raise ValueError(
                    "CSV is missing required columns: " + ", ".join(missing)
                )

            reader.fieldnames = headers

            for row in reader:
                rows_read += 1

                if rows_read > MAX_IMPORT_ROWS:
                    raise ValueError(
                        f"CSV exceeds the {MAX_IMPORT_ROWS}-row limit"
                    )

                if None in row:
                    errors.append(
                        f"row {reader.line_num}: too many fields"
                    )
                    continue

                try:
                    candles.append(_parse_row(row))
                except (TypeError, ValueError) as error:
                    if len(errors) < MAX_REPORTED_ERRORS:
                        errors.append(
                            f"row {reader.line_num}: {error}"
                        )

    except csv.Error as error:
        raise ValueError(f"Invalid CSV format: {error}") from error

    if rows_read == 0:
        raise ValueError("CSV contains a header but no data rows")

    if not errors:
        timestamps = [c["timestamp"] for c in candles]

        parsed = [
            datetime.fromisoformat(t.replace("Z", "+00:00"))
            for t in timestamps
        ]

        if len(set(timestamps)) != len(timestamps):
            errors.append("CSV contains duplicate timestamps")
        elif parsed != sorted(parsed):
            errors.append("CSV timestamps must be chronological")

    if errors:
        return {
            "status": "REJECTED",
            "file": path.name,
            "rows_read": rows_read,
            "inserted": 0,
            "duplicates": 0,
            "rejected": rows_read,
            "errors": errors,
        }

    try:
        result = ingest_candles(
            instrument=instrument,
            timeframe=timeframe,
            candles=candles,
            source=source,
            source_version=source_version,
            session_convention=session_convention,
        )
    except ValueError as error:
        return {
            "status": "REJECTED",
            "file": path.name,
            "rows_read": rows_read,
            "inserted": 0,
            "duplicates": 0,
            "rejected": rows_read,
            "errors": [str(error)],
        }

    return {
        "status": "IMPORTED",
        "file": path.name,
        "rows_read": rows_read,
        "inserted": result["inserted"],
        "duplicates": result["duplicates"],
        "rejected": 0,
        "errors": [],
    }
