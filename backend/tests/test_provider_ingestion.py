import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.data import historical_store
from app.data.provider_contract import (
    ProviderMetadata,
    ProviderRequest,
    ProviderNotApproved,
    ProviderRateLimited,
    ProviderResponseError,
)
from app.data.provider_ingestion import ingest_provider_batch


class FakeProvider:
    def __init__(
        self,
        data,
        licensing="APPROVED",
        version="v1",
        price_component="MID",
    ):
        self.data = data
        self.calls = 0
        self.metadata = ProviderMetadata(
            provider_id="test-provider",
            provider_version=version,
            instrument_mapping={"XAUUSD": "GOLD-SPOT"},
            supported_timeframes=frozenset({"M15", "H1", "H4", "D1"}),
            session_convention="UTC calendar boundaries",
            price_component=price_component,
            volume_semantics="provider tick volume",
            licensing_status=licensing,
        )

    def fetch_candles(self, request):
        self.calls += 1
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


def sample_observations():
    return [
        {
            "timestamp": "2026-01-01T01:00:00+01:00",
            "open": 2600,
            "high": 2610,
            "low": 2590,
            "close": 2605,
            "volume": 0,
        },
        {
            "timestamp": "2026-01-01T01:15:00+01:00",
            "open": 2605,
            "high": 2615,
            "low": 2600,
            "close": 2610,
        },
    ]


def sample_request():
    return ProviderRequest(
        instrument="XAUUSD",
        timeframe="M15",
        start="2026-01-01T00:00:00Z",
        end="2026-01-01T02:00:00Z",
    )


class ProviderIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "provider-test.db"
        self.db_patch = patch.object(
            historical_store, "DB_PATH", self.db
        )
        self.db_patch.start()
        self.approval_path = Path(self.temp.name) / "test-approval.json"
        self.approval_path.write_text(json.dumps({
            "provider_id": "test-provider",
            "status": "APPROVED",
            "approval_reference": "TEST-ONLY-APPROVAL",
            "approved_at": "2026-01-01",
            "expires_at": "2027-01-01",
            "permitted_instruments": ["XAUUSD"],
            "permitted_timeframes": ["M15", "H1", "H4", "D1"],
            "exchange_timezone": "UTC",
            "retention_days": 365,
            "commercial_use": True,
            "non_us_market_data_approval": True,
            "historical_storage": True,
            "derived_data_storage": True,
            "derived_data_display": True,
            "raw_data_redistribution": False,
            "attribution_required": True,
            "attribution_text": "Test fixture only"
        }), encoding="utf-8")
        historical_store.initialise_historical_store()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_approved_provider_ingests_canonical_utc_data(self):
        provider = FakeProvider(sample_observations())

        result = ingest_provider_batch(provider, sample_request(), approval_path=self.approval_path)

        self.assertEqual(result["status"], "INGESTED")
        self.assertEqual(result["inserted"], 2)

        rows = historical_store.get_historical_candles(
            "XAUUSD", "M15"
        )
        self.assertEqual(
            rows[0]["timestamp"],
            "2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(rows[0]["price_component"], "MID")
        self.assertEqual(rows[0]["volume"], 0.0)
        self.assertEqual(rows[0]["volume_available"], 1)
        self.assertEqual(rows[1]["volume_available"], 0)

    def test_unapproved_provider_is_blocked_before_fetch(self):
        provider = FakeProvider(
            sample_observations(),
            licensing="PENDING_REVIEW",
        )

        with self.assertRaises(ProviderNotApproved):
            ingest_provider_batch(provider, sample_request(), approval_path=self.approval_path)

        self.assertEqual(provider.calls, 0)
        self.assertEqual(
            historical_store.get_historical_candles(
                "XAUUSD", "M15"
            ),
            [],
        )

    def test_malformed_response_writes_nothing(self):
        bad = sample_observations()
        bad[1]["high"] = 1
        provider = FakeProvider(bad)

        with self.assertRaises(ProviderResponseError):
            ingest_provider_batch(provider, sample_request(), approval_path=self.approval_path)

        self.assertEqual(
            historical_store.get_historical_candles(
                "XAUUSD", "M15"
            ),
            [],
        )

    def test_duplicate_utc_timestamps_are_rejected(self):
        bad = sample_observations()
        bad[1]["timestamp"] = "2026-01-01T00:00:00Z"
        provider = FakeProvider(bad)

        with self.assertRaises(ProviderResponseError):
            ingest_provider_batch(provider, sample_request(), approval_path=self.approval_path)

        self.assertEqual(
            historical_store.get_historical_candles(
                "XAUUSD", "M15"
            ),
            [],
        )

    def test_naive_provider_timestamp_is_rejected(self):
        bad = sample_observations()
        bad[0]["timestamp"] = "2026-01-01T00:00:00"
        provider = FakeProvider(bad)

        with self.assertRaises(ProviderResponseError):
            ingest_provider_batch(provider, sample_request(), approval_path=self.approval_path)

    def test_rate_limit_error_is_preserved(self):
        provider = FakeProvider(
            ProviderRateLimited("rate limit")
        )

        with self.assertRaises(ProviderRateLimited):
            ingest_provider_batch(provider, sample_request(), approval_path=self.approval_path)

    def test_replay_is_idempotent(self):
        provider = FakeProvider(sample_observations())

        first = ingest_provider_batch(provider, sample_request(), approval_path=self.approval_path)
        second = ingest_provider_batch(provider, sample_request(), approval_path=self.approval_path)

        self.assertEqual(first["inserted"], 2)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["duplicates"], 2)
        self.assertEqual(
            len(
                historical_store.get_historical_candles(
                    "XAUUSD", "M15"
                )
            ),
            2,
        )

    def test_changed_provider_version_cannot_relabel_existing_data(self):
        first = FakeProvider(
            sample_observations(), version="v1"
        )
        second = FakeProvider(
            sample_observations(), version="v2"
        )

        ingest_provider_batch(first, sample_request(), approval_path=self.approval_path)

        with self.assertRaisesRegex(
            ValueError, "conflicting provenance"
        ):
            ingest_provider_batch(second, sample_request(), approval_path=self.approval_path)

        rows = historical_store.get_historical_candles(
            "XAUUSD", "M15"
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(
            all(row["source_version"] == "v1" for row in rows)
        )

    def test_invalid_request_range_is_rejected_before_fetch(self):
        provider = FakeProvider(sample_observations())
        invalid = ProviderRequest(
            "XAUUSD",
            "M15",
            "2026-01-01T02:00:00Z",
            "2026-01-01T01:00:00Z",
        )

        with self.assertRaisesRegex(ValueError, "before end"):
            ingest_provider_batch(provider, invalid, approval_path=self.approval_path)

        self.assertEqual(provider.calls, 0)


    def test_volume_marked_available_without_value_is_rejected(self):
        observations = sample_observations()
        observations[1]["volume_available"] = True
        provider = FakeProvider(observations)

        with self.assertRaisesRegex(
            ProviderResponseError, "volume is marked available"
        ):
            ingest_provider_batch(provider, sample_request(), approval_path=self.approval_path)

        self.assertEqual(
            historical_store.get_historical_candles("XAUUSD", "M15"),
            [],
        )


    def test_missing_written_approval_blocks_provider_fetch(self):
        provider = FakeProvider(sample_observations())
        missing_path = Path(self.temp.name) / "missing-approval.json"

        with self.assertRaises(ProviderNotApproved):
            ingest_provider_batch(
                provider,
                sample_request(),
                approval_path=missing_path,
            )

        self.assertEqual(provider.calls, 0)
        self.assertEqual(
            historical_store.get_historical_candles("XAUUSD", "M15"),
            [],
        )

    def test_candle_outside_requested_range_is_rejected(self):
        observations = sample_observations()
        observations[1]["timestamp"] = "2026-01-01T03:15:00+01:00"
        provider = FakeProvider(observations)

        with self.assertRaisesRegex(
            ProviderResponseError,
            "outside the requested range",
        ):
            ingest_provider_batch(
                provider,
                sample_request(),
                approval_path=self.approval_path,
            )

        self.assertEqual(provider.calls, 1)
        self.assertEqual(
            historical_store.get_historical_candles("XAUUSD", "M15"),
            [],
        )


    def test_future_dated_candle_is_rejected(self):
        observations = sample_observations()
        observations[0]["timestamp"] = "2099-01-01T00:00:00Z"
        observations[1]["timestamp"] = "2099-01-01T00:15:00Z"
        provider = FakeProvider(observations)

        with self.assertRaisesRegex(
            ProviderResponseError, "future-dated candle"
        ):
            ingest_provider_batch(
                provider,
                sample_request(),
                approval_path=self.approval_path,
            )

        self.assertEqual(
            historical_store.get_historical_candles("XAUUSD", "M15"), []
        )


if __name__ == "__main__":
    unittest.main()
