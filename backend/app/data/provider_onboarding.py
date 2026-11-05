"""Persistent provider inventory and sanitized connection-test history.

No API-key values are accepted or stored. Registration and successful
connectivity tests never grant licensing approval or signal eligibility.
"""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.data.provider_contract import (
    SUPPORTED_INSTRUMENTS,
    SUPPORTED_TIMEFRAMES,
)

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "oracle_fx.db"

ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
CODE_RE = re.compile(r"^[A-Z0-9_]{1,64}$")

LIFECYCLE_STATES = {"PENDING_REVIEW", "ACTIVE", "DISABLED"}
TEST_OUTCOMES = {
    "SUCCEEDED", "FAILED", "AUTH_FAILED",
    "RATE_LIMITED", "TIMEOUT", "NOT_RUN",
}


@contextmanager
def _connect():
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


def _timestamp(value=None):
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if not isinstance(value, str):
        raise ValueError("timestamp must be ISO-8601 text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("timestamp must be valid ISO-8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _scope(values, allowed, field):
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError(f"{field} must be a list")
    result = sorted(set(values))
    if any(not isinstance(value, str) for value in result):
        raise ValueError(f"{field} contains an invalid value")
    if not set(result).issubset(allowed):
        raise ValueError(f"{field} contains unsupported values")
    return result


def _migrate_provider_lifecycle(db):
    """Expand the provider lifecycle constraint without activating old rows."""
    row = db.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name='provider_onboarding'"
    ).fetchone()

    if row is None or "'ACTIVE'" in (row["sql"] or "").upper():
        return

    db.execute("""
        CREATE TABLE provider_onboarding_new (
            provider_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            adapter_key TEXT NOT NULL,
            lifecycle_state TEXT NOT NULL
                CHECK(lifecycle_state IN (
                    'PENDING_REVIEW','ACTIVE','DISABLED'
                )),
            credential_env_var TEXT NOT NULL,
            supported_instruments_json TEXT NOT NULL,
            historical_timeframes_json TEXT NOT NULL,
            live_timeframes_json TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    db.execute("""
        INSERT INTO provider_onboarding_new (
            provider_id, display_name, adapter_key, lifecycle_state,
            credential_env_var, supported_instruments_json,
            historical_timeframes_json, live_timeframes_json,
            notes, created_at, updated_at
        )
        SELECT
            provider_id, display_name, adapter_key, lifecycle_state,
            credential_env_var, supported_instruments_json,
            historical_timeframes_json, live_timeframes_json,
            notes, created_at, updated_at
        FROM provider_onboarding
    """)

    db.execute("DROP TABLE provider_onboarding")
    db.execute(
        "ALTER TABLE provider_onboarding_new "
        "RENAME TO provider_onboarding"
    )


def initialise_provider_onboarding_store():
    """Create additive tables in the existing ORACLE FX database."""
    with _connect() as db:
        db.execute("PRAGMA foreign_keys=OFF")
        _migrate_provider_lifecycle(db)
        db.execute("""
            CREATE TABLE IF NOT EXISTS provider_onboarding (
                provider_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                adapter_key TEXT NOT NULL,
                lifecycle_state TEXT NOT NULL
                    CHECK(lifecycle_state IN ('PENDING_REVIEW','ACTIVE','DISABLED')),
                credential_env_var TEXT NOT NULL,
                supported_instruments_json TEXT NOT NULL,
                historical_timeframes_json TEXT NOT NULL,
                live_timeframes_json TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS provider_connection_tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN (
                    'SUCCEEDED','FAILED','AUTH_FAILED',
                    'RATE_LIMITED','TIMEOUT','NOT_RUN'
                )),
                tested_at TEXT NOT NULL,
                latency_ms INTEGER,
                credential_present INTEGER NOT NULL
                    CHECK(credential_present IN (0,1)),
                diagnostic_code TEXT,
                FOREIGN KEY(provider_id)
                    REFERENCES provider_onboarding(provider_id)
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_provider_test_history
            ON provider_connection_tests(provider_id, tested_at DESC)
        """)
        db.execute("""
            CREATE TRIGGER IF NOT EXISTS provider_tests_no_update
            BEFORE UPDATE ON provider_connection_tests
            BEGIN
                SELECT RAISE(ABORT, 'provider test history is append-only');
            END
        """)
        db.execute("""
            CREATE TRIGGER IF NOT EXISTS provider_tests_no_delete
            BEFORE DELETE ON provider_connection_tests
            BEGIN
                SELECT RAISE(ABORT, 'provider test history is append-only');
            END
        """)


def _provider_dict(row):
    if row is None:
        return None
    item = dict(row)
    item["supported_instruments"] = json.loads(
        item.pop("supported_instruments_json")
    )
    item["historical_timeframes"] = json.loads(
        item.pop("historical_timeframes_json")
    )
    item["live_timeframes"] = json.loads(
        item.pop("live_timeframes_json")
    )
    item["credentials_configured"] = False
    item["licensing_approved"] = False
    item["signal_eligible"] = False
    return item


def register_provider(
    provider_id,
    display_name,
    adapter_key,
    credential_env_var,
    supported_instruments,
    historical_timeframes,
    live_timeframes=(),
    notes="",
):
    """Register metadata only. Every new provider starts pending review."""
    if not isinstance(provider_id, str) or not ID_RE.fullmatch(provider_id):
        raise ValueError("invalid provider_id")
    for value, field in (
        (display_name, "display_name"),
        (adapter_key, "adapter_key"),
    ):
        if not isinstance(value, str) or not value.strip() or len(value) > 120:
            raise ValueError(f"invalid {field}")
    if (
        not isinstance(credential_env_var, str)
        or not ENV_RE.fullmatch(credential_env_var)
    ):
        raise ValueError("credential_env_var must be an environment-variable name")
    if not isinstance(notes, str) or len(notes) > 1000:
        raise ValueError("notes must be text up to 1000 characters")

    instruments = _scope(
        supported_instruments, SUPPORTED_INSTRUMENTS, "supported_instruments"
    )
    historical = _scope(
        historical_timeframes, SUPPORTED_TIMEFRAMES, "historical_timeframes"
    )
    live = _scope(live_timeframes, SUPPORTED_TIMEFRAMES, "live_timeframes")

    if not instruments or not historical:
        raise ValueError("instrument and historical timeframe scopes are required")
    if not set(live).issubset(historical):
        raise ValueError("live timeframes must be within historical timeframes")

    now = _timestamp()
    with _connect() as db:
        db.execute("""
            INSERT INTO provider_onboarding (
                provider_id, display_name, adapter_key, lifecycle_state,
                credential_env_var, supported_instruments_json,
                historical_timeframes_json, live_timeframes_json,
                notes, created_at, updated_at
            ) VALUES (?, ?, ?, 'PENDING_REVIEW', ?, ?, ?, ?, ?, ?, ?)
        """, (
            provider_id, display_name.strip(), adapter_key.strip(),
            credential_env_var, json.dumps(instruments),
            json.dumps(historical), json.dumps(live), notes.strip(), now, now,
        ))
    return get_provider(provider_id)


def get_provider(provider_id):
    with _connect() as db:
        row = db.execute(
            "SELECT * FROM provider_onboarding WHERE provider_id=?",
            (provider_id,),
        ).fetchone()
    return _provider_dict(row)


def list_providers(include_disabled=True):
    query = "SELECT * FROM provider_onboarding"
    if not include_disabled:
        query += " WHERE lifecycle_state != 'DISABLED'"
    query += " ORDER BY provider_id"
    with _connect() as db:
        rows = db.execute(query).fetchall()
    return [_provider_dict(row) for row in rows]


def set_provider_lifecycle(provider_id, lifecycle_state):
    """Lifecycle controls cannot approve licensing."""
    if lifecycle_state not in LIFECYCLE_STATES:
        raise ValueError("lifecycle_state must be PENDING_REVIEW, ACTIVE, or DISABLED")
    with _connect() as db:
        cursor = db.execute("""
            UPDATE provider_onboarding
            SET lifecycle_state=?, updated_at=?
            WHERE provider_id=?
        """, (lifecycle_state, _timestamp(), provider_id))
        if cursor.rowcount != 1:
            raise KeyError("provider not found")
    return get_provider(provider_id)


def record_connection_test(
    provider_id,
    outcome,
    credential_present,
    latency_ms=None,
    diagnostic_code=None,
    tested_at=None,
):
    """Record an observed result; this function does not perform network I/O."""
    if outcome not in TEST_OUTCOMES:
        raise ValueError("unsupported connection-test outcome")
    if not isinstance(credential_present, bool):
        raise ValueError("credential_present must be boolean")
    if latency_ms is not None and (
        not isinstance(latency_ms, int)
        or isinstance(latency_ms, bool)
        or not 0 <= latency_ms <= 600000
    ):
        raise ValueError("latency_ms must be between 0 and 600000")
    if diagnostic_code is not None and (
        not isinstance(diagnostic_code, str)
        or not CODE_RE.fullmatch(diagnostic_code)
    ):
        raise ValueError("diagnostic_code must be a sanitized uppercase code")

    tested_at = _timestamp(tested_at)
    with _connect() as db:
        exists = db.execute(
            "SELECT 1 FROM provider_onboarding WHERE provider_id=?",
            (provider_id,),
        ).fetchone()
        if exists is None:
            raise KeyError("provider not found")
        cursor = db.execute("""
            INSERT INTO provider_connection_tests (
                provider_id, outcome, tested_at, latency_ms,
                credential_present, diagnostic_code
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            provider_id, outcome, tested_at, latency_ms,
            int(credential_present), diagnostic_code,
        ))
        test_id = cursor.lastrowid
    return get_connection_test(test_id)


def _test_dict(row):
    if row is None:
        return None
    item = dict(row)
    item["credential_present"] = bool(item["credential_present"])
    item["connection_verified"] = item["outcome"] == "SUCCEEDED"
    item["signal_eligible"] = False
    return item


def get_connection_test(test_id):
    with _connect() as db:
        row = db.execute(
            "SELECT * FROM provider_connection_tests WHERE id=?",
            (test_id,),
        ).fetchone()
    return _test_dict(row)


def list_connection_tests(provider_id, limit=50):
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= 500
    ):
        raise ValueError("limit must be between 1 and 500")
    with _connect() as db:
        rows = db.execute("""
            SELECT * FROM provider_connection_tests
            WHERE provider_id=?
            ORDER BY tested_at DESC, id DESC
            LIMIT ?
        """, (provider_id, limit)).fetchall()
    return [_test_dict(row) for row in rows]
