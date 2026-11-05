import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.data.provider_contract import ProviderMetadata, ProviderRequest
from app.data import provider_pipeline_store as store


class FakeProvider:
    def __init__(self, provider_id="provider_one"):
        self.metadata = ProviderMetadata(
            provider_id=provider_id,
            provider_version="test-v1",
            instrument_mapping={"XAUUSD": "GOLD"},
            supported_timeframes=frozenset({"M15", "H1", "H4", "D1"}),
            session_convention="UTC",
            price_component="MID",
            volume_semantics="TEST_ONLY",
            licensing_status="APPROVED",
        )


def make_request():
    return ProviderRequest(
        instrument="XAUUSD",
        timeframe="M15",
        start="2026-01-01T00:00:00Z",
        end="2026-01-01T01:00:00Z",
    )


class ProviderPipelineStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_oracle_fx.db"
        self.db_patch = patch.object(store, "DB_PATH", self.db_path)
        self.db_patch.start()
        store.initialise_pipeline_store()
        self.provider = FakeProvider()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_create_job_persists_queued_state_and_creation_event(self):
        job, created = store.create_job(
            "test-job-key-0001", [self.provider], make_request()
        )

        self.assertTrue(created)
        self.assertEqual(job["status"], "QUEUED")
        self.assertEqual(job["instrument"], "XAUUSD")
        self.assertEqual(job["timeframe"], "M15")
        self.assertEqual(job["provider_ids"], ["provider_one"])

        events = store.list_job_events(job["job_id"])
        self.assertEqual([e["event_type"] for e in events], ["JOB_CREATED"])

        self.assertEqual(store.get_job(job["job_id"])["status"], "QUEUED")

    def test_identical_idempotency_key_returns_existing_job(self):
        first, created_first = store.create_job(
            "test-job-key-0002", [self.provider], make_request()
        )
        second, created_second = store.create_job(
            "test-job-key-0002", [self.provider], make_request()
        )

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(len(store.list_jobs()), 1)

    def test_reusing_key_for_different_request_is_rejected(self):
        store.create_job(
            "test-job-key-0003", [self.provider], make_request()
        )
        different_request = ProviderRequest(
            instrument="XAUUSD",
            timeframe="H1",
            start="2026-01-01T00:00:00Z",
            end="2026-01-01T01:00:00Z",
        )

        with self.assertRaises(store.IdempotencyConflict):
            store.create_job(
                "test-job-key-0003", [self.provider], different_request
            )

    def test_provider_order_is_part_of_idempotency_fingerprint(self):
        first = FakeProvider("provider_one")
        second = FakeProvider("provider_two")
        store.create_job(
            "test-job-key-0004", [first, second], make_request()
        )

        with self.assertRaises(store.IdempotencyConflict):
            store.create_job(
                "test-job-key-0004", [second, first], make_request()
            )

    def test_job_lifecycle_and_attempt_history_are_persisted(self):
        job, _ = store.create_job(
            "test-job-key-0005", [self.provider], make_request()
        )
        store.mark_job_running(job["job_id"])
        attempt_id = store.start_attempt(
            job["job_id"], "provider_one", 1
        )
        store.finish_attempt(
            job["job_id"],
            "provider_one",
            attempt_id,
            1,
            "RATE_LIMITED",
            "PROVIDER_RATE_LIMITED",
        )
        finished = store.finish_job(
            job["job_id"],
            "UNAVAILABLE",
            {"diagnostic_code": "ALL_PROVIDERS_UNAVAILABLE"},
        )

        self.assertEqual(finished["status"], "UNAVAILABLE")
        events = store.list_job_events(job["job_id"])
        self.assertEqual(
            [event["event_type"] for event in events],
            [
                "JOB_CREATED",
                "JOB_STARTED",
                "ATTEMPT_STARTED",
                "ATTEMPT_FINISHED",
                "JOB_FINISHED",
            ],
        )
        self.assertEqual(events[3]["outcome"], "RATE_LIMITED")
        self.assertEqual(
            events[3]["diagnostic_code"], "PROVIDER_RATE_LIMITED"
        )

    def test_attempt_cannot_finish_without_a_matching_start(self):
        job, _ = store.create_job(
            "test-job-key-0006", [self.provider], make_request()
        )
        store.mark_job_running(job["job_id"])

        with self.assertRaises(store.JobNotRunnable):
            store.finish_attempt(
                job["job_id"],
                "provider_one",
                "not-a-real-attempt",
                1,
                "FAILED",
            )

    def test_attempt_cannot_be_finished_twice(self):
        job, _ = store.create_job(
            "test-job-key-0007", [self.provider], make_request()
        )
        store.mark_job_running(job["job_id"])
        attempt_id = store.start_attempt(
            job["job_id"], "provider_one", 1
        )
        store.finish_attempt(
            job["job_id"], "provider_one", attempt_id, 1, "INGESTED"
        )

        with self.assertRaises(ValueError):
            store.finish_attempt(
                job["job_id"], "provider_one", attempt_id, 1, "INGESTED"
            )

    def test_restart_marks_running_job_interrupted_without_replaying(self):
        job, _ = store.create_job(
            "test-job-key-0008", [self.provider], make_request()
        )
        store.mark_job_running(job["job_id"])

        store.initialise_pipeline_store()

        recovered = store.get_job(job["job_id"])
        self.assertEqual(recovered["status"], "INTERRUPTED")

        events = store.list_job_events(job["job_id"])
        self.assertEqual(events[-1]["event_type"], "JOB_INTERRUPTED")
        self.assertEqual(events[-1]["diagnostic_code"], "APPLICATION_RESTART")

        duplicate, created = store.create_job(
            "test-job-key-0008", [self.provider], make_request()
        )
        self.assertFalse(created)
        self.assertEqual(duplicate["status"], "INTERRUPTED")

    def test_event_history_rejects_updates_and_deletes(self):
        job, _ = store.create_job(
            "test-job-key-0009", [self.provider], make_request()
        )

        with sqlite3.connect(self.db_path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE provider_ingestion_events "
                    "SET outcome='CHANGED' WHERE job_id=?",
                    (job["job_id"],),
                )

        with sqlite3.connect(self.db_path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "DELETE FROM provider_ingestion_events WHERE job_id=?",
                    (job["job_id"],),
                )

    def test_timezone_naive_request_is_rejected(self):
        naive_request = ProviderRequest(
            instrument="XAUUSD",
            timeframe="M15",
            start="2026-01-01T00:00:00",
            end="2026-01-01T01:00:00Z",
        )

        with self.assertRaises(ValueError):
            store.create_job(
                "test-job-key-0010", [self.provider], naive_request
            )

    def test_secret_like_exception_text_is_not_written_to_events(self):
        job, _ = store.create_job(
            "test-job-key-0011", [self.provider], make_request()
        )
        store.mark_job_running(job["job_id"])
        attempt_id = store.start_attempt(
            job["job_id"], "provider_one", 1
        )
        store.finish_attempt(
            job["job_id"],
            "provider_one",
            attempt_id,
            1,
            "FAILED",
            "PROVIDER_FAILURE",
        )

        events_text = str(store.list_job_events(job["job_id"]))
        self.assertNotIn("private provider detail", events_text)
        self.assertNotIn("api_key", events_text.lower())


if __name__ == "__main__":
    unittest.main()


class QueuedRestartRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "queued_restart.db"
        self.db_patch = patch.object(store, "DB_PATH", self.db_path)
        self.db_patch.start()
        store.initialise_pipeline_store()
        self.provider = FakeProvider()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_queued_job_is_interrupted_after_restart(self):
        job, created = store.create_job(
            "queued-restart-key-01", [self.provider], make_request()
        )
        self.assertTrue(created)

        store.initialise_pipeline_store()

        recovered = store.get_job(job["job_id"])
        self.assertEqual(recovered["status"], "INTERRUPTED")

        events = store.list_job_events(job["job_id"])
        self.assertEqual(events[-1]["event_type"], "JOB_INTERRUPTED")
        self.assertEqual(
            events[-1]["diagnostic_code"],
            "APPLICATION_RESTART_BEFORE_START",
        )


class ProviderPipelineStoreHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "hardening.db"
        self.db_patch = patch.object(store, "DB_PATH", self.db_path)
        self.db_patch.start()
        store.initialise_pipeline_store()
        self.provider = FakeProvider()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _running_job(self, key):
        job, _ = store.create_job(key, [self.provider], make_request())
        store.mark_job_running(job["job_id"])
        return job

    def test_result_summary_rejects_unknown_fields(self):
        job = self._running_job("hardening-key-0001")
        with self.assertRaises(ValueError):
            store.finish_job(
                job["job_id"],
                "FAILED",
                {"diagnostic_code": "SAFE_CODE", "private_data": "secret"},
            )

    def test_result_summary_rejects_invalid_field_types(self):
        job = self._running_job("hardening-key-0002")
        with self.assertRaises(ValueError):
            store.finish_job(
                job["job_id"], "FAILED", {"attempt_count": True}
            )

    def test_result_summary_accepts_only_safe_fields(self):
        job = self._running_job("hardening-key-0003")
        finished = store.finish_job(
            job["job_id"],
            "INGESTED",
            {
                "status": "INGESTED",
                "attempt_count": 1,
                "failover_used": False,
                "provider_id": "provider_one",
            },
        )
        self.assertEqual(finished["status"], "INGESTED")
        self.assertEqual(finished["result_summary"]["attempt_count"], 1)

    def test_start_attempt_rejects_invalid_provider_and_number(self):
        job = self._running_job("hardening-key-0004")
        with self.assertRaises(ValueError):
            store.start_attempt(job["job_id"], " ", 1)
        with self.assertRaises(ValueError):
            store.start_attempt(job["job_id"], "provider_one", True)

    def test_finish_attempt_rejects_invalid_attempt_number(self):
        job = self._running_job("hardening-key-0005")
        attempt_id = store.start_attempt(
            job["job_id"], "provider_one", 1
        )
        with self.assertRaises(ValueError):
            store.finish_attempt(
                job["job_id"],
                "provider_one",
                attempt_id,
                True,
                "INGESTED",
            )
