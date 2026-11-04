"""Separate append-only SQLite ledger for imported macro-calendar observations."""
import json
from contextlib import closing
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "oracle_fx_macro.db"


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialise_macro_store():
    with closing(get_connection()) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS macro_events (
                event_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                event_type TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                currency_codes TEXT NOT NULL,
                impact TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                actual REAL,
                forecast REAL,
                previous REAL,
                unit TEXT NOT NULL,
                imported_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TRIGGER IF NOT EXISTS macro_events_no_update
            BEFORE UPDATE ON macro_events BEGIN
                SELECT RAISE(ABORT, 'Macro event ledger is append-only');
            END
        """)
        connection.execute("""
            CREATE TRIGGER IF NOT EXISTS macro_events_no_delete
            BEFORE DELETE ON macro_events BEGIN
                SELECT RAISE(ABORT, 'Macro event ledger is append-only');
            END
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_macro_events_scheduled ON macro_events(scheduled_at)")
        connection.commit()


def insert_macro_events(events):
    inserted = ignored = 0
    with closing(get_connection()) as connection:
        for event in events:
            cursor = connection.execute("""
                INSERT OR IGNORE INTO macro_events (
                    event_id, title, event_type, scheduled_at, currency_codes,
                    impact, source_name, source_url, actual, forecast, previous,
                    unit, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event["event_id"], event["title"], event["event_type"],
                event["scheduled_at"], json.dumps(event["currency_codes"]),
                event["impact"], event["source_name"], event["source_url"],
                event.get("actual"), event.get("forecast"), event.get("previous"),
                event["unit"], event["imported_at"],
            ))
            if cursor.rowcount == 1:
                inserted += 1
            else:
                ignored += 1
        connection.commit()
    return {"inserted": inserted, "ignored_existing_ids": ignored}


def list_macro_events(start_at=None, end_at=None, currency=None, impact=None, limit=200):
    clauses, values = [], []
    if start_at:
        clauses.append("scheduled_at >= ?")
        values.append(start_at)
    if end_at:
        clauses.append("scheduled_at <= ?")
        values.append(end_at)
    if impact:
        clauses.append("impact = ?")
        values.append(impact)
    sql = "SELECT * FROM macro_events"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY scheduled_at ASC LIMIT ?"
    values.append(limit)
    with closing(get_connection()) as connection:
        rows = connection.execute(sql, values).fetchall()
    events = []
    for row in rows:
        item = dict(row)
        item["currency_codes"] = json.loads(item["currency_codes"])
        if currency and currency not in item["currency_codes"]:
            continue
        item["source_status"] = "UNVERIFIED"
        events.append(item)
    return events
