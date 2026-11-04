import sqlite3
import json
from contextlib import closing
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "oracle_fx.db"


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialise_database():
    with closing(get_connection()) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS forecasts (
                id TEXT PRIMARY KEY,
                instrument TEXT NOT NULL,
                created_at TEXT NOT NULL,
                horizon TEXT NOT NULL,
                reference_price REAL NOT NULL,
                analysis TEXT NOT NULL,
                status TEXT NOT NULL
            )
        """)

        connection.execute("""
            CREATE TRIGGER IF NOT EXISTS forecasts_no_update
            BEFORE UPDATE ON forecasts
            BEGIN
                SELECT RAISE(ABORT, 'Forecast ledger is append-only');
            END
        """)

        connection.execute("""
            CREATE TRIGGER IF NOT EXISTS forecasts_no_delete
            BEFORE DELETE ON forecasts
            BEGIN
                SELECT RAISE(ABORT, 'Forecast ledger is append-only');
            END
        """)
        connection.commit()


def save_forecast(record):
    with closing(get_connection()) as connection:
        connection.execute("""
            INSERT INTO forecasts
            (id, instrument, created_at, horizon,
             reference_price, analysis, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            record["id"],
            record["instrument"],
            record["created_at"],
            record["horizon"],
            record["reference_price"],
            json.dumps(record["analysis"]),
            record["status"]
        ))
        connection.commit()


def get_forecasts(limit=50):
    with closing(get_connection()) as connection:
        rows = connection.execute(
            "SELECT * FROM forecasts ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()

    results = []
    for row in rows:
        item = dict(row)
        item["analysis"] = json.loads(item["analysis"])
        results.append(item)

    return results
