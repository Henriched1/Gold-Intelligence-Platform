import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.data import provider_ingestion_worker as worker
from app.data import provider_job_submission
from app.data import provider_pipeline_store
from app.data.provider_contract import (
    ProviderRateLimited,
    ProviderResponseError,
    ProviderUnavailable,
)


def make_job(status="QUEUED", provider_ids=None):
    return {
        "job_id": "job-test-001",
        "status": status,
        "instrument": "XAUUSD",
        "timeframe": "M15",
        "request_start": "2026-01-01T00:00:00+00:00",
        "request_end": "2026-01-01T01:00:00+00:00",
        "provider_ids": provider_ids or ["provider_one"],
    }


def make_adapter(provider_id="provider_one", candles=None):
    metadata = SimpleNamespace(
        provider_id=provider_id,
        provider_version="test-v1",
        session_convention="UTC",
        price_component="MID",
    )
    adapter = SimpleNamespace(metadata=metadata)
    adapter.fetch_candles = Mock(
        return_value=candles if candles is not None else [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "open": 2000.0,
                "high": 2001.0,
                "low": 1999.0,
                "close": 2000.5,
            }
        ]
    )
    return adapter


class ProviderIngestionWorkerTests(unittest.TestCase):
    def setUp(self):
        self.job = make_job()
        self.running_job = dict(self.job)
        self.running_job["status"] = "RUNNING"

        self.patches = [
            patch.object(
                worker.provider_pipeline_store,
                "get_job",
                return_value=self.job,
            ),
            patch.object(
                worker.provider_pipeline_store,
                "mark_job_running",
                return_value=self.running_job,
            ),
            patch.object(
                worker.provider_pipeline_store,
                "start_attempt",
                side_effect=lambda job_id, provider_id, number:
                    f"attempt-{number}",
            ),
            patch.object(
                worker.provider_pipeline_store, "finish_attempt"
            ),
            patch.object(
                worker.provider_pipeline_store,
                "finish_job",
                side_effect=lambda job_id, status, summary: {
                    "job_id": job_id,
                    "status": status,
                    "result_summary": summary,
                },
            ),
            patch.object(
                worker.historical_store,
                "ingest_candles",
                return_value={
                    "inserted": 1,
                    "duplicates": 0,
                    "total_received": 1,
                },
            ),
            patch.object(
                worker.provider_job_submission,
                "_admit_provider",
            ),
        ]

        self.mocks = [item.start() for item in self.patches]
        (
            self.get_job,
            self.mark_running,
            self.start_attempt,
            self.finish_attempt,
            self.finish_job,
            self.ingest_candles,
            self.admit_provider,
        ) = self.mocks

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_successful_historical_ingestion(self):
        adapter = make_adapter()
        self.admit_provider.return_value = adapter

        result = worker.execute_ingestion_job(
            "job-test-001", environ={}
        )

        self.assertEqual(result["job"]["status"], "INGESTED")
        self.assertTrue(result["historical_data_ingested"])
        self.assertFalse(result["trade_execution_enabled"])
        self.assertFalse(result["live_trading_enabled"])
        self.assertFalse(result["orders_submitted"])
        self.ingest_candles.assert_called_once()
        self.assertEqual(
            self.finish_attempt.call_args.args[4], "INGESTED"
        )

    def test_licensing_admission_rejection_blocks_fetch(self):
        adapter = make_adapter()
        adapter.fetch_candles = Mock()
        self.admit_provider.side_effect = (
            provider_job_submission.ProviderAdmissionError(
                "WRITTEN_LICENSE_APPROVAL_REQUIRED",
                "Written approval required.",
            )
        )

        result = worker.execute_ingestion_job(
            "job-test-001", environ={}
        )

        adapter.fetch_candles.assert_not_called()
        self.ingest_candles.assert_not_called()
        self.assertEqual(result["job"]["status"], "BLOCKED")
        self.assertEqual(
            result["provider_failures"][0]["diagnostic_code"],
            "WRITTEN_LICENSE_APPROVAL_REQUIRED",
        )

    def test_failover_uses_second_provider_after_first_unavailable(self):
        self.job["provider_ids"] = ["provider_one", "provider_two"]
        self.running_job["provider_ids"] = [
            "provider_one", "provider_two"
        ]
        second_adapter = make_adapter("provider_two")
        self.admit_provider.side_effect = [
            ProviderUnavailable("temporary outage"),
            second_adapter,
        ]

        result = worker.execute_ingestion_job(
            "job-test-001", environ={}
        )

        self.assertEqual(result["job"]["status"], "INGESTED")
        self.assertEqual(
            result["job"]["result_summary"]["attempt_count"], 2
        )
        self.assertTrue(
            result["job"]["result_summary"]["failover_used"]
        )
        self.assertEqual(
            result["job"]["result_summary"]["provider_id"],
            "provider_two",
        )
        self.assertEqual(len(result["provider_failures"]), 1)

    def test_rate_limit_is_recorded_as_unavailable(self):
        self.admit_provider.side_effect = ProviderRateLimited(
            "rate limited"
        )

        result = worker.execute_ingestion_job(
            "job-test-001", environ={}
        )

        self.assertEqual(result["job"]["status"], "UNAVAILABLE")
        self.assertEqual(
            result["provider_failures"][0]["outcome"],
            "RATE_LIMITED",
        )
        self.assertEqual(
            result["provider_failures"][0]["diagnostic_code"],
            "PROVIDER_RATE_LIMITED",
        )

    def test_empty_provider_response_is_rejected(self):
        adapter = make_adapter(candles=[])
        self.admit_provider.return_value = adapter

        result = worker.execute_ingestion_job(
            "job-test-001", environ={}
        )

        self.ingest_candles.assert_not_called()
        self.assertEqual(result["job"]["status"], "FAILED")
        self.assertEqual(
            result["provider_failures"][0]["outcome"], "REJECTED"
        )

    def test_malformed_candles_fail_store_validation_safely(self):
        adapter = make_adapter()
        self.admit_provider.return_value = adapter
        self.ingest_candles.side_effect = ValueError(
            "invalid candle payload"
        )

        result = worker.execute_ingestion_job(
            "job-test-001", environ={}
        )

        self.assertEqual(result["job"]["status"], "FAILED")
        self.assertEqual(
            result["provider_failures"][0]["diagnostic_code"],
            "REQUEST_OR_DATA_VALIDATION_FAILED",
        )

    def test_already_running_job_cannot_be_executed_again(self):
        self.get_job.return_value = make_job(status="RUNNING")

        with self.assertRaises(provider_pipeline_store.JobNotRunnable):
            worker.execute_ingestion_job(
                "job-test-001", environ={}
            )

        self.mark_running.assert_not_called()
        self.start_attempt.assert_not_called()
        self.admit_provider.assert_not_called()

    def test_missing_job_is_rejected(self):
        self.get_job.return_value = None

        with self.assertRaises(KeyError):
            worker.execute_ingestion_job(
                "missing-job", environ={}
            )

        self.mark_running.assert_not_called()

    def test_provider_failure_does_not_expose_exception_details(self):
        self.admit_provider.side_effect = RuntimeError(
            "private api_key=DO_NOT_LEAK"
        )

        result = worker.execute_ingestion_job(
            "job-test-001", environ={}
        )

        self.assertEqual(result["job"]["status"], "FAILED")
        self.assertNotIn(
            "DO_NOT_LEAK", str(result["provider_failures"])
        )
        self.assertNotIn(
            "api_key", str(result["provider_failures"]).lower()
        )


class ProviderIngestionWorkerSQLiteIntegrationTests(unittest.TestCase):
    def test_real_sqlite_job_and_historical_candle_ingestion(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from app.data.provider_contract import ProviderMetadata, ProviderRequest

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pipeline_db = root / "pipeline.sqlite3"
            historical_db = root / "historical.sqlite3"

            metadata = ProviderMetadata(
                provider_id="integration_provider",
                provider_version="integration-test-v1",
                instrument_mapping={"XAUUSD": "XAU/USD"},
                supported_timeframes=frozenset({"M15"}),
                session_convention="UTC",
                price_component="MID",
                volume_semantics="tick",
                licensing_status="APPROVED",
            )

            adapter = make_adapter("integration_provider")
            adapter.metadata = metadata

            request = ProviderRequest(
                instrument="XAUUSD",
                timeframe="M15",
                start="2026-01-01T00:00:00+00:00",
                end="2026-01-01T01:00:00+00:00",
            )

            with (
                patch.object(provider_pipeline_store, "DB_PATH", pipeline_db),
                patch.object(worker.historical_store, "DB_PATH", historical_db),
                patch.object(
                    worker.provider_job_submission,
                    "_admit_provider",
                    return_value=adapter,
                ),
            ):
                provider_pipeline_store.initialise_pipeline_store()
                worker.historical_store.initialise_historical_store()

                created_job, created = provider_pipeline_store.create_job(
                    "sqlite-integration-test-001",
                    [adapter],
                    request,
                )

                self.assertTrue(created)
                self.assertEqual(created_job["status"], "QUEUED")

                result = worker.execute_ingestion_job(
                    created_job["job_id"],
                    environ={},
                )

                self.assertEqual(result["job"]["status"], "INGESTED")
                self.assertTrue(result["historical_data_ingested"])
                self.assertEqual(result["stored"]["inserted"], 1)
                self.assertFalse(result["trade_execution_enabled"])
                self.assertFalse(result["live_trading_enabled"])
                self.assertFalse(result["orders_submitted"])

                persisted_job = provider_pipeline_store.get_job(
                    created_job["job_id"]
                )
                self.assertEqual(persisted_job["status"], "INGESTED")
                self.assertEqual(
                    persisted_job["result_summary"]["provider_id"],
                    "integration_provider",
                )

                candles = worker.historical_store.get_historical_candles(
                    "XAUUSD",
                    "M15",
                    source="integration_provider",
                )
                self.assertEqual(len(candles), 1)
                self.assertEqual(candles[0]["close"], 2000.5)
                self.assertEqual(
                    candles[0]["source_version"],
                    "integration-test-v1",
                )
                self.assertEqual(candles[0]["price_component"], "MID")

                events = provider_pipeline_store.list_job_events(
                    created_job["job_id"]
                )
                event_types = [event["event_type"] for event in events]

                self.assertEqual(
                    event_types,
                    [
                        "JOB_CREATED",
                        "JOB_STARTED",
                        "ATTEMPT_STARTED",
                        "ATTEMPT_FINISHED",
                        "JOB_FINISHED",
                    ],
                )
                self.assertEqual(events[-1]["outcome"], "INGESTED")
                adapter.fetch_candles.assert_called_once_with(request)


if __name__ == "__main__":
    unittest.main()
