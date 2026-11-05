"""Persistent, idempotent historical-ingestion job and event store."""

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.data.provider_contract import (
    ProviderRequest,
    SUPPORTED_INSTRUMENTS,
    SUPPORTED_TIMEFRAMES,
)

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "oracle_fx.db"

IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
DIAGNOSTIC_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

JOB_STATES = {
    "QUEUED", "RUNNING", "INGESTED", "UNAVAILABLE",
    "BLOCKED", "FAILED", "INTERRUPTED",
}
FINAL_STATES = {
    "INGESTED", "UNAVAILABLE", "BLOCKED", "FAILED",
}
ATTEMPT_OUTCOMES = {
    "INGESTED", "RATE_LIMITED", "UNAVAILABLE",
    "BLOCKED", "REJECTED", "FAILED",
}


class IdempotencyConflict(ValueError):
    """An idempotency key was reused for a different request."""


class JobNotRunnable(ValueError):
    """A job is not in a state that permits execution."""


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _validate_timestamp(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be ISO-8601 text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{field} must be valid ISO-8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _validate_job_input(idempotency_key, providers, request):
    if (
        not isinstance(idempotency_key, str)
        or not IDEMPOTENCY_RE.fullmatch(idempotency_key)
    ):
        raise ValueError("invalid idempotency_key")

    if not isinstance(providers, (list, tuple)) or not providers:
        raise ValueError("providers must be a non-empty list or tuple")
    if len(providers) > 5:
        raise ValueError("provider count exceeds the configured limit")

    provider_ids = []
    for provider in providers:
        metadata = getattr(provider, "metadata", None)
        provider_id = getattr(metadata, "provider_id", None)
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("every provider must have a valid provider_id")
        provider_ids.append(provider_id)

    if len(set(provider_ids)) != len(provider_ids):
        raise ValueError("duplicate provider_id values are not allowed")

    if not isinstance(request, ProviderRequest):
        raise ValueError("request must be a ProviderRequest")
    if request.instrument not in SUPPORTED_INSTRUMENTS:
        raise ValueError("unsupported canonical instrument")
    if request.timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError("unsupported canonical timeframe")

    start = _validate_timestamp(request.start, "request.start")
    end = _validate_timestamp(request.end, "request.end")
    if start >= end:
        raise ValueError("request start must be before end")

    payload = {
        "instrument": request.instrument,
        "timeframe": request.timeframe,
        "start": start,
        "end": end,
        "provider_ids": provider_ids,
    }
    fingerprint = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return provider_ids, payload, fingerprint


def _append_event(
    db,
    job_id,
    event_type,
    provider_id=None,
    attempt_id=None,
    attempt_number=None,
    outcome=None,
    diagnostic_code=None,
):
    db.execute(
        """
        INSERT INTO provider_ingestion_events (
            job_id, event_type, provider_id, attempt_id,
            attempt_number, outcome, diagnostic_code, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id, event_type, provider_id, attempt_id,
            attempt_number, outcome, diagnostic_code, _now(),
        ),
    )


def initialise_pipeline_store():
    """Create additive tables and recover jobs interrupted by a restart."""
    with _connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_ingestion_jobs (
                job_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_fingerprint TEXT NOT NULL,
                instrument TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                request_start TEXT NOT NULL,
                request_end TEXT NOT NULL,
                provider_ids_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'QUEUED','RUNNING','INGESTED','UNAVAILABLE',
                    'BLOCKED','FAILED','INTERRUPTED'
                )),
                result_summary_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_created
            ON provider_ingestion_jobs(created_at DESC)
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_ingestion_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK(event_type IN (
                    'JOB_CREATED','JOB_STARTED','ATTEMPT_STARTED',
                    'ATTEMPT_FINISHED','JOB_FINISHED','JOB_INTERRUPTED'
                )),
                provider_id TEXT,
                attempt_id TEXT,
                attempt_number INTEGER,
                outcome TEXT,
                diagnostic_code TEXT,
                occurred_at TEXT NOT NULL,
                FOREIGN KEY(job_id)
                    REFERENCES provider_ingestion_jobs(job_id)
            )
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ingestion_events_job
            ON provider_ingestion_events(job_id, event_id)
            """
        )
        db.execute(
            """
            CREATE TRIGGER IF NOT EXISTS ingestion_events_no_update
            BEFORE UPDATE ON provider_ingestion_events
            BEGIN
                SELECT RAISE(ABORT, 'ingestion event history is append-only');
            END
            """
        )
        db.execute(
            """
            CREATE TRIGGER IF NOT EXISTS ingestion_events_no_delete
            BEFORE DELETE ON provider_ingestion_events
            BEGIN
                SELECT RAISE(ABORT, 'ingestion event history is append-only');
            END
            """
        )

        interrupted = db.execute(
            """
            SELECT job_id, status FROM provider_ingestion_jobs
            WHERE status IN ('QUEUED', 'RUNNING')
            """
        ).fetchall()

        for row in interrupted:
            now = _now()
            previous_status = row["status"]
            diagnostic = (
                "APPLICATION_RESTART_BEFORE_START"
                if previous_status == "QUEUED"
                else "APPLICATION_RESTART"
            )
            db.execute(
                """
                UPDATE provider_ingestion_jobs
                SET status='INTERRUPTED', updated_at=?, finished_at=?
                WHERE job_id=? AND status=?
                """,
                (now, now, row["job_id"], previous_status),
            )
            _append_event(
                db, row["job_id"], "JOB_INTERRUPTED",
                outcome="INTERRUPTED",
                diagnostic_code=diagnostic,
            )


def create_job(idempotency_key, providers, request):
    """Create a job once; identical duplicate submissions never execute twice."""
    provider_ids, payload, fingerprint = _validate_job_input(
        idempotency_key, providers, request
    )
    now = _now()
    job_id = str(uuid.uuid4())

    with _connect() as db:
        # Serialize competing submissions before checking the unique key.
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            """
            SELECT * FROM provider_ingestion_jobs
            WHERE idempotency_key=?
            """,
            (idempotency_key,),
        ).fetchone()

        if existing is not None:
            if existing["request_fingerprint"] != fingerprint:
                raise IdempotencyConflict(
                    "idempotency key was already used for a different request"
                )
            return _job_dict(existing), False

        db.execute(
            """
            INSERT INTO provider_ingestion_jobs (
                job_id, idempotency_key, request_fingerprint,
                instrument, timeframe, request_start, request_end,
                provider_ids_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?)
            """,
            (
                job_id, idempotency_key, fingerprint,
                payload["instrument"], payload["timeframe"],
                payload["start"], payload["end"],
                json.dumps(provider_ids), now, now,
            ),
        )
        _append_event(db, job_id, "JOB_CREATED", outcome="QUEUED")

        row = db.execute(
            "SELECT * FROM provider_ingestion_jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()

    return _job_dict(row), True


def _job_dict(row):
    if row is None:
        return None
    item = dict(row)
    item["provider_ids"] = json.loads(item.pop("provider_ids_json"))
    item.pop("request_fingerprint", None)
    item.pop("idempotency_key", None)
    summary = item.pop("result_summary_json")
    item["result_summary"] = json.loads(summary) if summary else None
    return item


def get_job(job_id):
    with _connect() as db:
        row = db.execute(
            "SELECT * FROM provider_ingestion_jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
    return _job_dict(row)


def list_jobs(limit=50):
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= 500
    ):
        raise ValueError("limit must be between 1 and 500")
    with _connect() as db:
        rows = db.execute(
            """
            SELECT * FROM provider_ingestion_jobs
            ORDER BY created_at DESC, job_id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_job_dict(row) for row in rows]


def mark_job_running(job_id):
    now = _now()
    with _connect() as db:
        cursor = db.execute(
            """
            UPDATE provider_ingestion_jobs
            SET status='RUNNING', started_at=?, updated_at=?
            WHERE job_id=? AND status='QUEUED'
            """,
            (now, now, job_id),
        )
        if cursor.rowcount != 1:
            raise JobNotRunnable("job is not queued or does not exist")
        _append_event(db, job_id, "JOB_STARTED", outcome="RUNNING")
    return get_job(job_id)


def start_attempt(job_id, provider_id, attempt_number):
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ValueError("provider_id must be non-empty text")
    if (
        not isinstance(attempt_number, int)
        or isinstance(attempt_number, bool)
        or attempt_number < 1
    ):
        raise ValueError("attempt_number must be a positive integer")

    attempt_id = str(uuid.uuid4())
    with _connect() as db:
        row = db.execute(
            "SELECT status FROM provider_ingestion_jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if row is None or row["status"] != "RUNNING":
            raise JobNotRunnable("job is not running")
        _append_event(
            db, job_id, "ATTEMPT_STARTED",
            provider_id=provider_id,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            outcome="STARTED",
        )
    return attempt_id

def finish_attempt(
    job_id,
    provider_id,
    attempt_id,
    attempt_number,
    outcome,
    diagnostic_code=None,
):
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ValueError("provider_id must be non-empty text")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise ValueError("attempt_id must be non-empty text")
    if (
        not isinstance(attempt_number, int)
        or isinstance(attempt_number, bool)
        or attempt_number < 1
    ):
        raise ValueError("attempt_number must be a positive integer")
    if not isinstance(outcome, str) or outcome not in ATTEMPT_OUTCOMES:
        raise ValueError("unsupported attempt outcome")
    if diagnostic_code is not None and (
        not isinstance(diagnostic_code, str)
        or not DIAGNOSTIC_RE.fullmatch(diagnostic_code)
    ):
        raise ValueError("diagnostic_code must be a sanitized uppercase code")

    with _connect() as db:
        job = db.execute(
            "SELECT status FROM provider_ingestion_jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
        started = db.execute(
            """
            SELECT 1 FROM provider_ingestion_events
            WHERE job_id=? AND event_type='ATTEMPT_STARTED'
              AND provider_id=? AND attempt_id=? AND attempt_number=?
            """,
            (job_id, provider_id, attempt_id, attempt_number),
        ).fetchone()
        if job is None or job["status"] != "RUNNING" or started is None:
            raise JobNotRunnable("matching running attempt was not found")

        already_finished = db.execute(
            """
            SELECT 1 FROM provider_ingestion_events
            WHERE job_id=? AND event_type='ATTEMPT_FINISHED'
              AND attempt_id=?
            """,
            (job_id, attempt_id),
        ).fetchone()
        if already_finished is not None:
            raise ValueError("attempt has already been finished")

        _append_event(
            db, job_id, "ATTEMPT_FINISHED",
            provider_id=provider_id,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            outcome=outcome,
            diagnostic_code=diagnostic_code,
        )

def finish_job(job_id, status, result_summary=None):
    if not isinstance(status, str) or status not in FINAL_STATES:
        raise ValueError("unsupported final job status")
    if result_summary is None:
        result_summary = {}
    if not isinstance(result_summary, dict):
        raise ValueError("result_summary must be a dictionary")

    allowed_fields = {
        "status", "attempt_count", "failover_used",
        "diagnostic_code", "provider_id",
    }
    if set(result_summary) - allowed_fields:
        raise ValueError("result_summary contains unsupported fields")

    summary_status = result_summary.get("status")
    if summary_status is not None and summary_status != status:
        raise ValueError("summary status must match final job status")

    attempt_count = result_summary.get("attempt_count")
    if attempt_count is not None and (
        not isinstance(attempt_count, int)
        or isinstance(attempt_count, bool)
        or attempt_count < 0
    ):
        raise ValueError("attempt_count must be a non-negative integer")

    failover_used = result_summary.get("failover_used")
    if failover_used is not None and not isinstance(failover_used, bool):
        raise ValueError("failover_used must be a boolean")

    diagnostic_code = result_summary.get("diagnostic_code")
    if diagnostic_code is not None and (
        not isinstance(diagnostic_code, str)
        or not DIAGNOSTIC_RE.fullmatch(diagnostic_code)
    ):
        raise ValueError("diagnostic_code must be a sanitized uppercase code")

    provider_id = result_summary.get("provider_id")
    if provider_id is not None and (
        not isinstance(provider_id, str)
        or not provider_id.strip()
        or len(provider_id) > 128
    ):
        raise ValueError("provider_id must be non-empty text of at most 128 characters")

    safe_summary = json.dumps(
        result_summary,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(safe_summary) > 4000:
        raise ValueError("result summary is too large")

    now = _now()
    with _connect() as db:
        cursor = db.execute(
            """
            UPDATE provider_ingestion_jobs
            SET status=?, result_summary_json=?, updated_at=?, finished_at=?
            WHERE job_id=? AND status='RUNNING'
            """,
            (status, safe_summary, now, now, job_id),
        )
        if cursor.rowcount != 1:
            raise JobNotRunnable("job is not running")
        _append_event(
            db, job_id, "JOB_FINISHED",
            outcome=status,
            diagnostic_code=diagnostic_code,
        )
    return get_job(job_id)

def list_job_events(job_id):
    with _connect() as db:
        exists = db.execute(
            "SELECT 1 FROM provider_ingestion_jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if exists is None:
            raise KeyError("job not found")
        rows = db.execute(
            """
            SELECT event_id, event_type, provider_id, attempt_id,
                   attempt_number, outcome, diagnostic_code, occurred_at
            FROM provider_ingestion_events
            WHERE job_id=? ORDER BY event_id
            """,
            (job_id,),
        ).fetchall()
    return [dict(row) for row in rows]
