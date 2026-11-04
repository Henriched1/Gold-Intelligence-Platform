import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.data.provider_approval import (
    validate_live_approval,
)
from app.data.provider_contract import (
    ProviderMetadata,
    ProviderNotApproved,
)
from app.data.source_registry import get_data_source_readiness
from app.core.universe import INSTRUMENTS
from app.data.provider_contract import SUPPORTED_TIMEFRAMES
from app.data.providers.twelve_data import TwelveDataProvider


def approval_record():
    return {
        "provider_id": "twelve_data",
        "status": "APPROVED",
        "approval_reference": "TEST-ONLY",
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
        "attribution_text": "Test fixture",
    }


class FakeProvider:
    def __init__(self):
        self._secret = "never-return-this"
        self.credentials_configured = True
        self.metadata = ProviderMetadata(
            provider_id="test-provider",
            provider_version="test-v1",
            instrument_mapping={"XAUUSD": "XAU/USD"},
            supported_timeframes=frozenset({"M15", "H1"}),
            session_convention="UTC",
            price_component="UNKNOWN",
            volume_semantics="Unavailable",
            licensing_status="APPROVED",
            live_supported_timeframes=frozenset(),
        )


class Phase14DataInfrastructureTests(unittest.TestCase):
    def test_live_approval_requires_separate_live_rights(self):
        record = approval_record()

        with self.assertRaises(ProviderNotApproved):
            validate_live_approval(
                record,
                "XAUUSD",
                "M15",
                today=date(2026, 11, 4),
            )

        record["live_market_data_access"] = True
        record["live_data_storage"] = True

        approved = validate_live_approval(
            record,
            "XAUUSD",
            "M15",
            today=date(2026, 11, 4),
        )
        self.assertIs(approved, record)

    def test_historical_approval_does_not_imply_live_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "approval.json"
            path.write_text(
                json.dumps(approval_record()),
                encoding="utf-8",
            )
            provider = TwelveDataProvider(
                approval_path=path,
                api_key="test-only-secret",
            )

        self.assertEqual(
            provider.metadata.licensing_status,
            "APPROVED",
        )
        self.assertEqual(
            provider.metadata.supported_timeframes,
            frozenset({"M15", "H1", "H4", "D1"}),
        )
        self.assertEqual(
            provider.metadata.live_supported_timeframes,
            frozenset(),
        )

    def test_live_approved_timeframes_are_separate(self):
        record = approval_record()
        record["live_market_data_access"] = True
        record["live_data_storage"] = True

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "approval.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            provider = TwelveDataProvider(
                approval_path=path,
                api_key="test-only-secret",
            )

        self.assertEqual(
            provider.metadata.live_supported_timeframes,
            frozenset({"M15", "H1", "H4", "D1"}),
        )

    def test_readiness_matrix_covers_global_universe_and_timeframes(self):
        provider = FakeProvider()
        report = get_data_source_readiness(lambda: provider)

        expected = len(INSTRUMENTS) * len(SUPPORTED_TIMEFRAMES)
        self.assertEqual(
            len(report["instrument_timeframe_matrix"]),
            expected,
        )
        self.assertEqual(report["market_universe_count"], len(INSTRUMENTS))
        self.assertEqual(
            report["timeframe_count"],
            len(SUPPORTED_TIMEFRAMES),
        )

    def test_unmapped_global_instrument_is_not_claimed_as_covered(self):
        report = get_data_source_readiness(FakeProvider)
        row = next(
            item for item in report["instrument_timeframe_matrix"]
            if item["instrument"] == "EURUSD"
            and item["timeframe"] == "M15"
        )
        self.assertEqual(
            row["historical_data_status"],
            "NO_REGISTERED_PROVIDER",
        )
        self.assertEqual(
            row["live_data_status"],
            "NO_REGISTERED_PROVIDER",
        )
        self.assertFalse(row["signal_eligible"])

    def test_historical_scope_does_not_make_live_scope_ready(self):
        report = get_data_source_readiness(FakeProvider)
        row = next(
            item for item in report["instrument_timeframe_matrix"]
            if item["instrument"] == "XAUUSD"
            and item["timeframe"] == "M15"
        )
        self.assertEqual(
            row["historical_data_status"],
            "HISTORICAL_CONNECTION_NOT_TESTED",
        )
        self.assertEqual(
            row["live_data_status"],
            "LIVE_LICENSING_NOT_APPROVED",
        )

    def test_readiness_never_calls_network_or_exposes_credentials(self):
        with patch(
            "app.data.providers.twelve_data.urlopen"
        ) as mocked_urlopen:
            report = get_data_source_readiness(FakeProvider)

        mocked_urlopen.assert_not_called()
        self.assertEqual(report["network_requests_made"], 0)
        self.assertFalse(report["live_data_connected"])
        self.assertFalse(report["trade_execution"])
        self.assertNotIn("never-return-this", str(report))
        self.assertNotIn("api_key", str(report).lower())


if __name__ == "__main__":
    unittest.main()


class MultiProviderRegistryTests(unittest.TestCase):
    def test_provider_registry_registers_and_removes_adapters(self):
        from app.data.provider_registry import (
            register_provider_factory,
            unregister_provider_factory,
            get_registered_provider_factories,
        )

        register_provider_factory("temporary-test", FakeProvider)
        try:
            self.assertIn(
                "temporary-test",
                get_registered_provider_factories(),
            )
        finally:
            self.assertTrue(
                unregister_provider_factory("temporary-test")
            )

    def test_duplicate_registration_is_rejected(self):
        from app.data.provider_registry import register_provider_factory

        register_provider_factory("duplicate-test", FakeProvider)
        try:
            with self.assertRaises(ValueError):
                register_provider_factory("duplicate-test", FakeProvider)
        finally:
            from app.data.provider_registry import unregister_provider_factory
            unregister_provider_factory("duplicate-test")

    def test_readiness_accepts_multiple_provider_factories(self):
        from app.data.source_registry import get_data_source_readiness

        class SecondProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.metadata = ProviderMetadata(
                    provider_id="second-provider",
                    provider_version="second-v1",
                    instrument_mapping={"EURUSD": "EUR/USD"},
                    supported_timeframes=frozenset({"M15", "H1"}),
                    session_convention="UTC",
                    price_component="UNKNOWN",
                    volume_semantics="Unavailable",
                    licensing_status="NOT_APPROVED",
                )

        report = get_data_source_readiness(
            provider_factories={
                "first": FakeProvider,
                "second": SecondProvider,
            }
        )

        self.assertEqual(report["provider_count"], 2)
        row = next(
            item for item in report["instrument_timeframe_matrix"]
            if item["instrument"] == "EURUSD"
            and item["timeframe"] == "M15"
        )
        self.assertEqual(row["provider_ids"], ["second-provider"])
        self.assertFalse(row["signal_eligible"])
        self.assertEqual(report["network_requests_made"], 0)
