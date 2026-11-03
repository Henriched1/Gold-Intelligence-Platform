"""Isolated, append-only SQLite store for historical-data provider pilots."""

import json
import sqlite3
from pathlib import Path

from app.data.historical_store import DB_PATH as PRODUCTION_DB_PATH


def _safe_path(database_path):
    if database_path is None:
        raise ValueError("an explicit pilot database path is required")

    path = Path(database_path).expanduser().resolve()
    production = Path(PRODUCTION_DB_PATH).expanduser().resolve()

    if path == production:
        raise ValueError("pilot database cannot be the production historical database")

    if path.exists() and path.is_dir():
        raise ValueError("pilot database path must be a file")

    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect_pilot(database_path):
    path = _safe_path(database_path)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialise_pilot_database(database_path):
    connection = connect_pilot(database_path)
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS acquisition_plans (
                plan_id TEXT PRIMARY KEY,
                plan_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS acquisition_chunks (
                plan_id TEXT NOT NULL,
                chunk_no INTEGER NOT NULL,
                start_utc TEXT NOT NULL,
                end_utc TEXT NOT NULL,
                bar_slots INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'PLANNED',
                returned_rows INTEGER NOT NULL DEFAULT 0,
                duplicate_rows INTEGER NOT NULL DEFAULT 0,
                error_type TEXT,
                completed_at TEXT,
                PRIMARY KEY (plan_id, chunk_no),
                FOREIGN KEY (plan_id) REFERENCES acquisition_plans(plan_id)
            );

            CREATE TABLE IF NOT EXISTS pilot_candles (
                instrument TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                volume_available INTEGER NOT NULL,
                source TEXT NOT NULL,
                source_version TEXT NOT NULL,
                session_convention TEXT NOT NULL,
                price_component TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                chunk_no INTEGER NOT NULL,
                PRIMARY KEY (
                    instrument, timeframe, timestamp, source
                ),
                FOREIGN KEY (plan_id, chunk_no)
                    REFERENCES acquisition_chunks(plan_id, chunk_no)
            );

            CREATE INDEX IF NOT EXISTS idx_pilot_candles_range
            ON pilot_candles(instrument, timeframe, timestamp);

            CREATE TRIGGER IF NOT EXISTS pilot_candles_no_update
            BEFORE UPDATE ON pilot_candles
            BEGIN
                SELECT RAISE(ABORT, 'pilot candles are append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS pilot_candles_no_delete
            BEFORE DELETE ON pilot_candles
            BEGIN
                SELECT RAISE(ABORT, 'pilot candles are append-only');
            END;
        """)
        connection.commit()
    finally:
        connection.close()

    return str(_safe_path(database_path))


def register_plan(database_path, plan):
    required = ("plan_id", "instrument", "timeframe", "chunks")
    if not isinstance(plan, dict) or any(key not in plan for key in required):
        raise ValueError("invalid acquisition plan")

    if not isinstance(plan["chunks"], list) or not plan["chunks"]:
        raise ValueError("acquisition plan has no chunks")

    plan_json = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    connection = connect_pilot(database_path)

    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT plan_json FROM acquisition_plans WHERE plan_id = ?",
            (plan["plan_id"],),
        ).fetchone()

        if existing and existing["plan_json"] != plan_json:
            raise ValueError("plan ID already exists with different plan content")

        connection.execute(
            "INSERT OR IGNORE INTO acquisition_plans(plan_id, plan_json) VALUES (?, ?)",
            (plan["plan_id"], plan_json),
        )

        for chunk in plan["chunks"]:
            number = int(chunk["chunk"])
            start = str(chunk["start"])
            end = str(chunk["end"])
            slots = int(chunk["bar_slots"])

            if number < 1 or slots < 1 or slots > 5000:
                raise ValueError("invalid or oversized acquisition chunk")

            connection.execute(
                """
                INSERT OR IGNORE INTO acquisition_chunks
                (plan_id, chunk_no, start_utc, end_utc, bar_slots)
                VALUES (?, ?, ?, ?, ?)
                """,
                (plan["plan_id"], number, start, end, slots),
            )

            saved = connection.execute(
                """
                SELECT start_utc, end_utc, bar_slots
                FROM acquisition_chunks
                WHERE plan_id = ? AND chunk_no = ?
                """,
                (plan["plan_id"], number),
            ).fetchone()

            if tuple(saved) != (start, end, slots):
                raise ValueError("saved chunk differs from acquisition plan")

        count = connection.execute(
            "SELECT COUNT(*) FROM acquisition_chunks WHERE plan_id = ?",
            (plan["plan_id"],),
        ).fetchone()[0]

        if count != len(plan["chunks"]):
            raise ValueError("saved chunk count differs from acquisition plan")

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_chunk_statuses(database_path, plan_id):
    connection = connect_pilot(database_path)
    try:
        rows = connection.execute(
            """
            SELECT chunk_no, status, returned_rows, duplicate_rows, error_type
            FROM acquisition_chunks
            WHERE plan_id = ?
            ORDER BY chunk_no
            """,
            (plan_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def mark_chunk_running(database_path, plan_id, chunk_no):
    connection = connect_pilot(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT status FROM acquisition_chunks
            WHERE plan_id = ? AND chunk_no = ?
            """,
            (plan_id, chunk_no),
        ).fetchone()

        if row is None:
            raise ValueError("chunk is not registered")

        if row["status"] == "COMPLETE":
            connection.commit()
            return False

        connection.execute(
            """
            UPDATE acquisition_chunks
            SET status = 'RUNNING', error_type = NULL
            WHERE plan_id = ? AND chunk_no = ?
            """,
            (plan_id, chunk_no),
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def mark_chunk_failed(database_path, plan_id, chunk_no, error):
    connection = connect_pilot(database_path)
    try:
        connection.execute(
            """
            UPDATE acquisition_chunks
            SET status = 'FAILED', error_type = ?
            WHERE plan_id = ? AND chunk_no = ?
              AND status != 'COMPLETE'
            """,
            (type(error).__name__, plan_id, chunk_no),
        )
        connection.commit()
    finally:
        connection.close()


def commit_chunk(
    database_path, plan, chunk_no, candles, provider_metadata
):
    connection = connect_pilot(database_path)
    inserted = 0
    duplicates = 0

    try:
        connection.execute("BEGIN IMMEDIATE")

        for candle in candles:
            values = (
                plan["instrument"],
                plan["timeframe"],
                candle["timestamp"],
                candle["open"],
                candle["high"],
                candle["low"],
                candle["close"],
                candle["volume"],
                int(candle["volume_available"]),
                provider_metadata.provider_id,
                provider_metadata.provider_version,
                provider_metadata.session_convention,
                provider_metadata.price_component,
                plan["plan_id"],
                chunk_no,
            )

            existing = connection.execute(
                """
                SELECT open, high, low, close, volume, volume_available,
                       source_version, session_convention, price_component
                FROM pilot_candles
                WHERE instrument = ? AND timeframe = ?
                  AND timestamp = ? AND source = ?
                """,
                (
                    plan["instrument"], plan["timeframe"],
                    candle["timestamp"], provider_metadata.provider_id,
                ),
            ).fetchone()

            if existing:
                expected = (
                    candle["open"], candle["high"], candle["low"],
                    candle["close"], candle["volume"],
                    int(candle["volume_available"]),
                    provider_metadata.provider_version,
                    provider_metadata.session_convention,
                    provider_metadata.price_component,
                )
                if tuple(existing) != expected:
                    raise ValueError(
                        "conflicting candle or provenance already exists"
                    )
                duplicates += 1
                continue

            connection.execute(
                """
                INSERT INTO pilot_candles (
                    instrument, timeframe, timestamp,
                    open, high, low, close, volume, volume_available,
                    source, source_version, session_convention,
                    price_component, plan_id, chunk_no
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            inserted += 1

        connection.execute(
            """
            UPDATE acquisition_chunks
            SET status = 'COMPLETE',
                returned_rows = ?,
                duplicate_rows = ?,
                error_type = NULL,
                completed_at = CURRENT_TIMESTAMP
            WHERE plan_id = ? AND chunk_no = ?
            """,
            (inserted + duplicates, duplicates, plan["plan_id"], chunk_no),
        )

        connection.commit()
        return {"inserted": inserted, "duplicates": duplicates}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
