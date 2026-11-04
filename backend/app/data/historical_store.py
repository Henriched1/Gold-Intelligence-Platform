"""Immutable, provider-neutral historical candle storage."""

import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "oracle_fx.db"

from app.data.provider_contract import (
    SUPPORTED_INSTRUMENTS,
    SUPPORTED_TIMEFRAMES,
)

REQUIRED_FIELDS = ("timestamp", "open", "high", "low", "close")


@contextmanager
def _connect():
    """Open a SQLite connection and always close it safely."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _utc_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO-8601 string")

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be valid ISO-8601") from error

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")

    return parsed.astimezone(timezone.utc).isoformat()


def _normalise_candle(candle):
    if not isinstance(candle, dict):
        raise ValueError("each candle must be an object")

    missing = [
        field for field in REQUIRED_FIELDS
        if field not in candle
    ]

    if missing:
        raise ValueError("missing candle fields: " + ", ".join(missing))

    timestamp = _utc_timestamp(candle["timestamp"])
    values = {}

    for field in ("open", "high", "low", "close"):
        try:
            number = float(candle[field])
        except (TypeError, ValueError) as error:
            raise ValueError(f"{field} must be numeric") from error

        if not math.isfinite(number):
            raise ValueError(f"{field} must be finite")

        values[field] = number

    volume_available = candle.get(
        "volume_available",
        "volume" in candle and candle.get("volume") is not None,
    )
    if not isinstance(volume_available, bool):
        raise ValueError("volume_available must be true or false")

    try:
        volume = float(candle.get("volume", 0))
    except (TypeError, ValueError) as error:
        raise ValueError("volume must be numeric") from error

    if not math.isfinite(volume) or volume < 0:
        raise ValueError("volume must be finite and non-negative")

    if values["high"] < max(
        values["open"], values["close"], values["low"]
    ):
        raise ValueError("high is inconsistent with OHLC values")

    if values["low"] > min(
        values["open"], values["close"], values["high"]
    ):
        raise ValueError("low is inconsistent with OHLC values")

    return (
        timestamp,
        values["open"],
        values["high"],
        values["low"],
        values["close"],
        volume,
        int(volume_available),
    )


def initialise_historical_store():
    with _connect() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                volume_available INTEGER,
                price_component TEXT NOT NULL DEFAULT 'UNKNOWN',
                source TEXT NOT NULL,
                source_version TEXT NOT NULL,
                session_convention TEXT NOT NULL,
                ingested_at TEXT NOT NULL,
                UNIQUE(instrument, timeframe, timestamp, source)
            )
        """)

        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(historical_candles)"
            ).fetchall()
        }
        if "volume_available" not in columns:
            connection.execute(
                "ALTER TABLE historical_candles "
                "ADD COLUMN volume_available INTEGER"
            )

        if "price_component" not in columns:
            connection.execute(
                "ALTER TABLE historical_candles "
                "ADD COLUMN price_component TEXT NOT NULL DEFAULT 'UNKNOWN'"
            )

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_historical_lookup
            ON historical_candles(
                instrument, timeframe, timestamp, source
            )
        """)

        connection.execute("""
            CREATE TRIGGER IF NOT EXISTS historical_candles_no_update
            BEFORE UPDATE ON historical_candles
            BEGIN
                SELECT RAISE(ABORT, 'historical candles are append-only');
            END
        """)

        connection.execute("""
            CREATE TRIGGER IF NOT EXISTS historical_candles_no_delete
            BEFORE DELETE ON historical_candles
            BEGIN
                SELECT RAISE(ABORT, 'historical candles are append-only');
            END
        """)


def ingest_candles(
    instrument,
    timeframe,
    candles,
    source,
    source_version="unspecified",
    session_convention="unspecified",
    price_component="UNKNOWN",
):
    instrument = str(instrument).upper()

    if instrument not in SUPPORTED_INSTRUMENTS:
        raise ValueError("unsupported instrument")

    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError("unsupported timeframe")

    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")

    if not isinstance(source_version, str) or not source_version.strip():
        raise ValueError("source_version is required")

    if not isinstance(session_convention, str) or not session_convention.strip():
        raise ValueError("session_convention is required")

    if price_component not in {"BID", "ASK", "MID", "UNKNOWN"}:
        raise ValueError("invalid price_component")

    if not isinstance(candles, list) or not candles:
        raise ValueError("candles must be a non-empty list")

    rows = [_normalise_candle(item) for item in candles]
    timestamps = [row[0] for row in rows]

    if timestamps != sorted(timestamps):
        raise ValueError("batch timestamps must be unique and chronological")

    if len(set(timestamps)) != len(timestamps):
        raise ValueError("batch timestamps must be unique and chronological")

    ingested_at = datetime.now(timezone.utc).isoformat()
    inserted = 0
    duplicates = 0

    with _connect() as connection:
        # Check every existing record before inserting any new record.
        for row in rows:
            existing = connection.execute("""
                SELECT open, high, low, close, volume, volume_available,
                       source_version, session_convention, price_component
                FROM historical_candles
                WHERE instrument=?
                  AND timeframe=?
                  AND timestamp=?
                  AND source=?
            """, (
                instrument,
                timeframe,
                row[0],
                source,
            )).fetchone()

            if existing is not None:
                existing_values = tuple(
                    existing[key]
                    for key in ("open", "high", "low", "close", "volume")
                )

                if (
                existing_values != row[1:6]
                or existing["volume_available"] != row[6]
            ):
                    raise ValueError(
                        f"conflicting candle already stored for "
                        f"{row[0]} from {source}"
                    )

                existing_metadata = (
                    existing["source_version"],
                    existing["session_convention"],
                    existing["price_component"],
                )
                incoming_metadata = (
                    source_version,
                    session_convention,
                    price_component,
                )

                if existing_metadata != incoming_metadata:
                    raise ValueError(
                        f"conflicting provenance already stored for "
                        f"{row[0]} from {source}"
                    )

                duplicates += 1

        for row in rows:
            cursor = connection.execute("""
                INSERT OR IGNORE INTO historical_candles (
                    instrument,
                    timeframe,
                    timestamp,
                    open,
                    high,
                    low,
                    close,
                    volume,
                    volume_available,
                    price_component,
                    source,
                    source_version,
                    session_convention,
                    ingested_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                instrument,
                timeframe,
                *row,
                price_component,
                source,
                source_version,
                session_convention,
                ingested_at,
            ))

            inserted += cursor.rowcount

    return {
        "inserted": inserted,
        "duplicates": duplicates,
        "total_received": len(rows),
    }


def get_historical_candles(
    instrument,
    timeframe,
    start=None,
    end=None,
    source=None,
    limit=5000,
):
    instrument = str(instrument).upper()

    if (
        instrument not in SUPPORTED_INSTRUMENTS
        or timeframe not in SUPPORTED_TIMEFRAMES
    ):
        raise ValueError("unsupported instrument or timeframe")

    if not isinstance(limit, int) or not 1 <= limit <= 100000:
        raise ValueError("limit must be between 1 and 100000")

    start_utc = _utc_timestamp(start) if start is not None else None
    end_utc = _utc_timestamp(end) if end is not None else None

    if start_utc is not None and end_utc is not None:
        if start_utc > end_utc:
            raise ValueError("start must not be after end")

    clauses = ["instrument=?", "timeframe=?"]
    params = [instrument, timeframe]

    if start_utc is not None:
        clauses.append("timestamp>=?")
        params.append(start_utc)

    if end_utc is not None:
        clauses.append("timestamp<=?")
        params.append(end_utc)

    if source is not None:
        clauses.append("source=?")
        params.append(source)

    params.append(limit)

    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM historical_candles WHERE "
            + " AND ".join(clauses)
            + " ORDER BY timestamp ASC, source ASC LIMIT ?",
            params,
        ).fetchall()

    return [dict(row) for row in rows]


def get_data_coverage(instrument, timeframe, source=None):
    instrument = str(instrument).upper()

    if (
        instrument not in SUPPORTED_INSTRUMENTS
        or timeframe not in SUPPORTED_TIMEFRAMES
    ):
        raise ValueError("unsupported instrument or timeframe")

    query = """
        SELECT
            COUNT(*) AS candle_count,
            MIN(timestamp) AS first_timestamp,
            MAX(timestamp) AS last_timestamp,
            COUNT(DISTINCT source) AS source_count
        FROM historical_candles
        WHERE instrument=? AND timeframe=?
    """

    params = [instrument, timeframe]

    if source is not None:
        query += " AND source=?"
        params.append(source)

    with _connect() as connection:
        row = connection.execute(query, params).fetchone()

    return {
        "instrument": instrument,
        "timeframe": timeframe,
        "candle_count": row["candle_count"],
        "first_timestamp": row["first_timestamp"],
        "last_timestamp": row["last_timestamp"],
        "source_count": row["source_count"],
        "gap_analysis": "NOT_CLASSIFIED_SESSION_CALENDAR_REQUIRED",
    }
