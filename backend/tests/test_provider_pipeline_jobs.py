import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.data.provider_contract import (
    ProviderMetadata,
    ProviderNotApproved,
    ProviderRateLimited,
    ProviderRequest,
)
from app.data import provider_pipeline_store as store
from app.data.provider_pipeline_jobs import run_persistent_ingestion


class FakeProvider:
    def __init__(self, provider_id):
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


class PersistentPipelineJobTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "pipeline_jobs.db"
        self.db_patch = patch.object(store, "DB_PATH", self.db_path)
        self.db_patch.start()
        store.initialise_pipeline_store()
        self.first = FakeProvider("provider_one")
        self.second = FakeProvider("provider_two")

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_success_records_job_and_attempt(self):
        calls = []

        def ingest(provider, request, approval_path):
            calls.append(provider.metadata.provider_id)
            return {"inserted": 3}

        result = run_persistent_ingestion(
            "persistent-job-key-001",
            [self.first],
            make_request(),
            ingest_fn=ingest,
        )

        self.assertTrue(result["executed"])
        self.assertEqual(result["job"]["status"], "INGESTED")
        self.assertEqual(calls, ["provider_one"])
        events = store.list_job_events(result["job"]["job_id"])
        self.assertEqual(
            [e["event_type"] for e in events],
            [
                "JOB_CREATED",
                "JOB_STARTED",
                "ATTEMPT_STARTED",
                "ATTEMPT_FINISHED",
                "JOB_FINISHED",
            ],
        )

    def test_retry_attempts_are_persisted(self):
        calls = []

        def ingest(provider, request, approval_path):
            calls.append(provider.metadata.provider_id)
            if len(calls) == 1:
                raise ProviderRateLimited("private provider message")
            return {"inserted": 2}

        result = run_persistent_ingestion(
            "persistent-job-key-002",
            [self.first],
            make_request(),
            ingest_fn=ingest,
            sleep_fn=lambda _: None,
        )

        self.assertEqual(result["job"]["status"], "INGESTED")
        events = store.list_job_events(result["job"]["job_id"])
        finished = [e for e in events if e["event_type"] == "ATTEMPT_FINISHED"]
        self.assertEqual(len(finished), 2)
        self.assertEqual(finished[0]["outcome"], "RATE_LIMITED")
        self.assertEqual(finished[1]["outcome"], "INGESTED")

    def test_failover_attempts_are_persisted(self):
        calls = []

        def ingest(provider, request, approval_path):
            calls.append(provider.metadata.provider_id)
            if provider is self.first:
                raise ProviderRateLimited("private detail")
            return {"inserted": 1}

        result = run_persistent_ingestion(
            "persistent-job-key-003",
            [self.first, self.second],
            make_request(),
            max_attempts_per_provider=1,
            backoff_base_seconds=0,
            ingest_fn=ingest,
            sleep_fn=lambda _: None,
        )

        self.assertEqual(result["job"]["status"], "INGESTED")
        self.assertEqual(calls, ["provider_one", "provider_two"])
        events = store.list_job_events(result["job"]["job_id"])
        attempts = [e for e in events if e["event_type"] == "ATTEMPT_FINISHED"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["outcome"], "RATE_LIMITED")
        self.assertEqual(attempts[1]["outcome"], "INGESTED")

    def test_duplicate_key_does_not_execute_provider_again(self):
        calls = []

        def ingest(provider, request, approval_path):
            calls.append("called")
            return {"inserted": 1}

        first = run_persistent_ingestion(
            "persistent-job-key-004",
            [self.first],
            make_request(),
            ingest_fn=ingest,
        )
        second = run_persistent_ingestion(
            "persistent-job-key-004",
            [self.first],
            make_request(),
            ingest_fn=ingest,
        )

        self.assertTrue(first["executed"])
        self.assertFalse(second["executed"])
        self.assertEqual(first["job"]["job_id"], second["job"]["job_id"])
        self.assertEqual(calls, ["called"])

    def test_licensing_failure_is_persisted_as_blocked(self):
        def ingest(provider, request, approval_path):
            raise ProviderNotApproved("private licensing details")

        result = run_persistent_ingestion(
            "persistent-job-key-005",
            [self.first, self.second],
            make_request(),
            ingest_fn=ingest,
        )

        self.assertEqual(result["job"]["status"], "BLOCKED")
        events = store.list_job_events(result["job"]["job_id"])
        attempts = [e for e in events if e["event_type"] == "ATTEMPT_FINISHED"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["diagnostic_code"], "PROVIDER_NOT_APPROVED")

    def test_unexpected_exception_is_sanitized_and_job_failed(self):
        def ingest(provider, request, approval_path):
            raise RuntimeError("secret api_key=do-not-store")

        with self.assertRaisesRegex(RuntimeError, "secret api_key"):
            run_persistent_ingestion(
                "persistent-job-key-006",
                [self.first],
                make_request(),
                ingest_fn=ingest,
            )

        job = store.list_jobs()[0]
        self.assertEqual(job["status"], "FAILED")
        events_text = str(store.list_job_events(job["job_id"]))
        self.assertNotIn("do-not-store", events_text)
        self.assertNotIn("secret api_key", events_text)
        self.assertIn("UNEXPECTED_ERROR", events_text)

    def test_reusing_key_with_different_request_is_rejected(self):
        run_persistent_ingestion(
            "persistent-job-key-007",
            [self.first],
            make_request(),
            ingest_fn=lambda *args, **kwargs: {"inserted": 1},
        )
        different = ProviderRequest(
            instrument="XAUUSD",
            timeframe="H1",
            start="2026-01-01T00:00:00Z",
            end="2026-01-01T01:00:00Z",
        )

        with self.assertRaises(store.IdempotencyConflict):
            run_persistent_ingestion(
                "persistent-job-key-007",
                [self.first],
                different,
                ingest_fn=lambda *args, **kwargs: {"inserted": 1},
            )


if __name__ == "__main__":
    unittest.main()
