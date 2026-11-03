import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app.data.provider_contract import (
    ProviderNotApproved,
    ProviderRateLimited,
    ProviderRequest,
)
from app.data.providers.twelve_data import TwelveDataProvider


def approved_record():
    return {
        "provider_id": "twelve_data",
        "status": "APPROVED",
        "approval_reference": "TEST-APPROVAL-001",
        "approved_at": "2026-11-01",
        "expires_at": "2027-11-01",
        "permitted_instruments": ["XAUUSD"],
        "permitted_timeframes": ["D1", "H4", "H1", "M15"],
        "exchange_timezone": "UTC",
        "retention_days": 365,
        "commercial_use": True,
        "non_us_market_data_approval": True,
        "historical_storage": True,
        "derived_data_storage": True,
        "derived_data_display": True,
        "raw_data_redistribution": False,
        "attribution_required": True,
        "attribution_text": "Twelve Data",
    }


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class TwelveDataProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.approval_path = (
            Path(self.temp_dir.name) / "approval.json"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_approval(self, record):
        self.approval_path.write_text(
            json.dumps(record),
            encoding="utf-8",
        )

    def request(self, timeframe="M15"):
        return ProviderRequest(
            instrument="XAUUSD",
            timeframe=timeframe,
            start="2026-11-02T00:00:00Z",
            end="2026-11-03T00:00:00Z",
        )

    def provider(self, api_key="test-key"):
        return TwelveDataProvider(
            approval_path=self.approval_path,
            api_key=api_key,
        )

    def test_missing_approval_disables_provider(self):
        provider = self.provider()

        self.assertEqual(
            provider.metadata.licensing_status,
            "NOT_APPROVED",
        )
        self.assertEqual(
            provider.metadata.supported_timeframes,
            frozenset(),
        )

    def test_unapproved_provider_never_calls_network(self):
        provider = self.provider()

        with patch(
            "app.data.providers.twelve_data.urlopen"
        ) as mocked_urlopen:
            with self.assertRaises(ProviderNotApproved):
                provider.fetch_candles(self.request())

        mocked_urlopen.assert_not_called()

    def test_expired_approval_never_calls_network(self):
        record = approved_record()
        record["expires_at"] = "2026-11-03"
        self.write_approval(record)
        provider = self.provider()

        with patch(
            "app.data.providers.twelve_data.urlopen"
        ) as mocked_urlopen:
            with self.assertRaises(ProviderNotApproved):
                provider.fetch_candles(self.request())

        mocked_urlopen.assert_not_called()

    def test_approved_mock_response_is_parsed(self):
        self.write_approval(approved_record())
        provider = self.provider()

        payload = {
            "meta": {"symbol": "XAU/USD"},
            "values": [
                {
                    "datetime": "2026-11-02 00:00:00",
                    "open": "4000.10",
                    "high": "4005.00",
                    "low": "3998.00",
                    "close": "4002.50",
                    "volume": "0",
                },
                {
                    "datetime": "2026-11-02 00:15:00",
                    "open": "4002.50",
                    "high": "4007.00",
                    "low": "4001.00",
                    "close": "4006.00",
                },
            ],
        }

        with patch(
            "app.data.providers.twelve_data.urlopen",
            return_value=FakeResponse(payload),
        ) as mocked_urlopen:
            candles = provider.fetch_candles(self.request())

        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0]["open"], "4000.10")
        self.assertEqual(candles[0]["volume"], "0")
        self.assertTrue(candles[0]["volume_available"])
        self.assertIsNone(candles[1]["volume"])
        self.assertFalse(candles[1]["volume_available"])

        call_args = mocked_urlopen.call_args
        url = call_args.args[0].full_url
        params = parse_qs(urlparse(url).query)

        self.assertEqual(params["symbol"], ["XAU/USD"])
        self.assertEqual(params["interval"], ["15min"])
        self.assertEqual(params["timezone"], ["UTC"])
        self.assertNotIn("test-key", url)
        self.assertIsNone(params.get("apikey"))
        self.assertEqual(
            call_args.args[0].get_header("Authorization"),
            "apikey test-key",
        )
        self.assertEqual(params["outputsize"], ["5000"])

    def test_single_candle_request_with_equal_boundaries_is_allowed(self):
        self.write_approval(approved_record())
        provider = self.provider()
        request = ProviderRequest(
            instrument="XAUUSD",
            timeframe="M15",
            start="2026-11-02T00:00:00Z",
            end="2026-11-02T00:00:00Z",
        )
        payload = {"values": [{
            "datetime": "2026-11-02 00:00:00",
            "open": "4000",
            "high": "4001",
            "low": "3999",
            "close": "4000.5",
        }]}
        with patch(
            "app.data.providers.twelve_data.urlopen",
            return_value=FakeResponse(payload),
        ) as mocked_urlopen:
            candles = provider.fetch_candles(request)
        self.assertEqual(len(candles), 1)
        mocked_urlopen.assert_called_once()

    def test_reversed_request_range_is_rejected_before_network(self):
        self.write_approval(approved_record())
        provider = self.provider()
        request = ProviderRequest(
            instrument="XAUUSD",
            timeframe="M15",
            start="2026-11-03T00:00:00Z",
            end="2026-11-02T00:00:00Z",
        )
        with patch(
            "app.data.providers.twelve_data.urlopen"
        ) as mocked_urlopen:
            with self.assertRaises(ValueError):
                provider.fetch_candles(request)
        mocked_urlopen.assert_not_called()

    def test_out_of_range_candle_is_rejected(self):
        from app.data.provider_contract import ProviderResponseError

        self.write_approval(approved_record())
        provider = self.provider()

        payload = {
            "values": [{
                "datetime": "2026-11-01 23:45:00",
                "open": "4000",
                "high": "4001",
                "low": "3999",
                "close": "4000",
            }]
        }

        with patch(
            "app.data.providers.twelve_data.urlopen",
            return_value=FakeResponse(payload),
        ):
            with self.assertRaises(ProviderResponseError):
                provider.fetch_candles(self.request())

    def test_duplicate_timestamps_are_rejected(self):
        from app.data.provider_contract import ProviderResponseError

        self.write_approval(approved_record())
        provider = self.provider()

        candle = {
            "datetime": "2026-11-02 00:15:00",
            "open": "4000",
            "high": "4001",
            "low": "3999",
            "close": "4000",
        }
        payload = {"values": [candle, candle.copy()]}

        with patch(
            "app.data.providers.twelve_data.urlopen",
            return_value=FakeResponse(payload),
        ):
            with self.assertRaises(ProviderResponseError):
                provider.fetch_candles(self.request())

    def test_non_chronological_response_is_rejected(self):
        from app.data.provider_contract import ProviderResponseError

        self.write_approval(approved_record())
        provider = self.provider()

        payload = {
            "values": [
                {
                    "datetime": "2026-11-02 00:30:00",
                    "open": "4000",
                    "high": "4001",
                    "low": "3999",
                    "close": "4000",
                },
                {
                    "datetime": "2026-11-02 00:15:00",
                    "open": "4000",
                    "high": "4001",
                    "low": "3999",
                    "close": "4000",
                },
            ]
        }

        with patch(
            "app.data.providers.twelve_data.urlopen",
            return_value=FakeResponse(payload),
        ):
            with self.assertRaises(ProviderResponseError):
                provider.fetch_candles(self.request())

    def test_daily_request_uses_approved_timezone(self):
        record = approved_record()
        record["exchange_timezone"] = "Africa/Lagos"
        self.write_approval(record)
        provider = self.provider()

        params = provider._build_params(
            self.request("D1"),
            record,
        )

        self.assertEqual(params["timezone"], "Africa/Lagos")

    def test_missing_api_key_prevents_network_call(self):
        self.write_approval(approved_record())
        provider = self.provider(api_key="")

        with patch(
            "app.data.providers.twelve_data.urlopen"
        ) as mocked_urlopen:
            from app.data.provider_contract import ProviderUnavailable

            with self.assertRaises(ProviderUnavailable):
                provider.fetch_candles(self.request())

        mocked_urlopen.assert_not_called()

    def test_unsupported_timeframe_is_rejected(self):
        self.write_approval(approved_record())
        provider = self.provider()

        request = ProviderRequest(
            instrument="XAUUSD",
            timeframe="M5",
            start="2026-11-02T00:00:00Z",
            end="2026-11-03T00:00:00Z",
        )

        with self.assertRaises(ValueError):
            provider.fetch_candles(request)

    def test_provider_rate_limit_is_classified(self):
        self.write_approval(approved_record())
        provider = self.provider()

        from urllib.error import HTTPError

        error = HTTPError(
            "https://example.invalid",
            429,
            "Rate limited",
            {},
            None,
        )

        with patch(
            "app.data.providers.twelve_data.urlopen",
            side_effect=error,
        ):
            with self.assertRaises(ProviderRateLimited):
                provider.fetch_candles(self.request())


if __name__ == "__main__":
    unittest.main()
