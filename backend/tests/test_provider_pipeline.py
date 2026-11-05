import unittest

from app.data.provider_contract import (
    ProviderMetadata,
    ProviderNotApproved,
    ProviderRateLimited,
    ProviderRequest,
    ProviderResponseError,
    ProviderUnavailable,
)
from app.data.provider_pipeline import ingest_with_failover


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


def request():
    return ProviderRequest(
        instrument="XAUUSD",
        timeframe="M15",
        start="2026-01-01T00:00:00Z",
        end="2026-01-01T01:00:00Z",
    )


class ProviderPipelineTests(unittest.TestCase):
    def setUp(self):
        self.first = FakeProvider("provider_one")
        self.second = FakeProvider("provider_two")

    def test_rate_limit_retries_then_succeeds(self):
        calls = []
        delays = []

        def ingest(provider, data_request, approval_path):
            calls.append(provider.metadata.provider_id)
            if len(calls) == 1:
                raise ProviderRateLimited("private provider detail")
            return {"inserted": 4}

        result = ingest_with_failover(
            [self.first],
            request(),
            ingest_fn=ingest,
            sleep_fn=delays.append,
        )

        self.assertEqual(result["status"], "INGESTED")
        self.assertEqual(calls, ["provider_one", "provider_one"])
        self.assertEqual(delays, [0.25])
        self.assertEqual(result["ingestion"]["inserted"], 4)
        self.assertEqual(
            result["attempts"][0]["diagnostic_code"],
            "PROVIDER_RATE_LIMITED",
        )

    def test_unavailable_provider_fails_over(self):
        calls = []

        def ingest(provider, data_request, approval_path):
            calls.append(provider.metadata.provider_id)
            if provider is self.first:
                raise ProviderUnavailable("private detail")
            return {"inserted": 2}

        result = ingest_with_failover(
            [self.first, self.second],
            request(),
            max_attempts_per_provider=2,
            backoff_base_seconds=0,
            ingest_fn=ingest,
            sleep_fn=lambda _: self.fail("zero backoff must not sleep"),
        )

        self.assertEqual(result["status"], "INGESTED")
        self.assertEqual(result["provider_id"], "provider_two")
        self.assertTrue(result["failover_used"])
        self.assertEqual(
            calls,
            ["provider_one", "provider_one", "provider_two"],
        )

    def test_backoff_is_exponential_and_capped(self):
        delays = []

        def ingest(provider, data_request, approval_path):
            raise ProviderUnavailable("private detail")

        result = ingest_with_failover(
            [self.first],
            request(),
            max_attempts_per_provider=3,
            backoff_base_seconds=4,
            backoff_cap_seconds=5,
            ingest_fn=ingest,
            sleep_fn=delays.append,
        )

        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(delays, [4, 5])
        self.assertEqual(len(result["attempts"]), 3)

    def test_license_failure_stops_without_failover(self):
        calls = []

        def ingest(provider, data_request, approval_path):
            calls.append(provider.metadata.provider_id)
            raise ProviderNotApproved("private detail")

        result = ingest_with_failover(
            [self.first, self.second],
            request(),
            ingest_fn=ingest,
            sleep_fn=lambda _: None,
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["diagnostic_code"], "PROVIDER_NOT_APPROVED")
        self.assertEqual(calls, ["provider_one"])
        self.assertNotIn("private detail", str(result))

    def test_invalid_response_stops_without_failover(self):
        calls = []

        def ingest(provider, data_request, approval_path):
            calls.append(provider.metadata.provider_id)
            raise ProviderResponseError("private response body")

        result = ingest_with_failover(
            [self.first, self.second],
            request(),
            ingest_fn=ingest,
            sleep_fn=lambda _: None,
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(
            result["diagnostic_code"],
            "INVALID_PROVIDER_RESPONSE",
        )
        self.assertEqual(calls, ["provider_one"])
        self.assertNotIn("private response body", str(result))

    def test_all_unavailable_returns_bounded_failure(self):
        calls = []

        def ingest(provider, data_request, approval_path):
            calls.append(provider.metadata.provider_id)
            raise ProviderUnavailable("private detail")

        result = ingest_with_failover(
            [self.first, self.second],
            request(),
            max_attempts_per_provider=1,
            backoff_base_seconds=0,
            ingest_fn=ingest,
            sleep_fn=lambda _: None,
        )

        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(
            result["diagnostic_code"],
            "ALL_PROVIDERS_UNAVAILABLE",
        )
        self.assertEqual(calls, ["provider_one", "provider_two"])

    def test_provider_specific_approval_paths_are_passed(self):
        received = []

        def ingest(provider, data_request, approval_path):
            received.append(approval_path)
            return {"inserted": 1}

        result = ingest_with_failover(
            [self.first],
            request(),
            approval_paths={"provider_one": "/secure/provider-one.json"},
            ingest_fn=ingest,
        )

        self.assertEqual(result["status"], "INGESTED")
        self.assertEqual(received, ["/secure/provider-one.json"])

    def test_duplicate_provider_ids_are_rejected(self):
        duplicate = FakeProvider("provider_one")

        with self.assertRaisesRegex(ValueError, "duplicate"):
            ingest_with_failover(
                [self.first, duplicate],
                request(),
                ingest_fn=lambda *args, **kwargs: {},
            )

    def test_provider_and_retry_limits_are_enforced(self):
        providers = [FakeProvider(f"provider_{i}") for i in range(6)]

        with self.assertRaisesRegex(ValueError, "provider count"):
            ingest_with_failover(
                providers,
                request(),
                ingest_fn=lambda *args, **kwargs: {},
            )

        with self.assertRaisesRegex(ValueError, "between 1 and 3"):
            ingest_with_failover(
                [self.first],
                request(),
                max_attempts_per_provider=4,
                ingest_fn=lambda *args, **kwargs: {},
            )

    def test_no_wait_occurs_after_final_attempt(self):
        delays = []

        def ingest(provider, data_request, approval_path):
            raise ProviderRateLimited("private detail")

        result = ingest_with_failover(
            [self.first],
            request(),
            max_attempts_per_provider=1,
            ingest_fn=ingest,
            sleep_fn=delays.append,
        )

        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(delays, [])


if __name__ == "__main__":
    unittest.main()
