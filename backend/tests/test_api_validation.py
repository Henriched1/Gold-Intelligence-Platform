import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app.main import AnalysisRequest, analyse_market, data_quality_report


def make_candles():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []

    for i in range(12):
        price = 2600 + i
        candles.append({
            "timestamp": (start + timedelta(minutes=15 * i)).isoformat(),
            "open": price,
            "high": price + 2,
            "low": price - 2,
            "close": price + 1,
            "volume": 100,
        })

    return candles


class ApiValidationTests(unittest.TestCase):

    def test_rejects_timezone_naive_timestamp(self):
        candles = make_candles()
        candles[0]["timestamp"] = "2026-01-01T00:00:00"

        request = AnalysisRequest(candles=candles)

        with self.assertRaises(HTTPException) as context:
            analyse_market(request)

        self.assertEqual(context.exception.status_code, 422)

    def test_rejects_non_finite_price(self):
        candles = make_candles()
        candles[-1]["close"] = float("nan")

        request = AnalysisRequest(candles=candles)

        with self.assertRaises(HTTPException) as context:
            analyse_market(request)

        self.assertEqual(context.exception.status_code, 422)

    def test_rejects_invalid_ohlc(self):
        candles = make_candles()
        candles[-1]["high"] = candles[-1]["open"] - 1

        request = AnalysisRequest(candles=candles)

        with self.assertRaises(HTTPException) as context:
            analyse_market(request)

        self.assertEqual(context.exception.status_code, 422)

    def test_valid_candles_reach_analysis(self):
        candles = make_candles()
        request = AnalysisRequest(candles=candles)
        expected = {"status": "RESEARCH_ONLY"}

        with patch(
            "app.main.analyse_structure",
            return_value=expected,
        ) as analysis_mock, patch(
            "app.main.save_forecast",
        ):
            result = analyse_market(request)

        analysis_mock.assert_called_once()
        self.assertEqual(result["analysis"], expected)
        self.assertIsNone(result["probability"])



    def test_data_quality_endpoint_returns_report(self):
        expected = {"status": "NO_DATA", "read_only": True}

        with patch(
            "app.main.generate_data_quality_report",
            return_value=expected,
        ) as report_mock:
            result = data_quality_report("XAUUSD", "M15")

        self.assertEqual(result, expected)
        report_mock.assert_called_once_with(
            instrument="XAUUSD",
            timeframe="M15",
        )

    def test_data_quality_endpoint_uses_default_parameters(self):
        expected = {"status": "NO_DATA"}

        with patch(
            "app.main.generate_data_quality_report",
            return_value=expected,
        ) as report_mock:
            result = data_quality_report()

        self.assertEqual(result, expected)
        report_mock.assert_called_once_with(
            instrument="XAUUSD",
            timeframe="M15",
        )

    def test_data_quality_invalid_instrument_returns_422(self):
        with patch(
            "app.main.generate_data_quality_report",
            side_effect=ValueError("unsupported instrument"),
        ):
            with self.assertRaises(HTTPException) as context:
                data_quality_report("EURUSD", "M15")

        self.assertEqual(context.exception.status_code, 422)

    def test_data_quality_invalid_timeframe_returns_422(self):
        with patch(
            "app.main.generate_data_quality_report",
            side_effect=ValueError("unsupported timeframe"),
        ):
            with self.assertRaises(HTTPException) as context:
                data_quality_report("XAUUSD", "M5")

        self.assertEqual(context.exception.status_code, 422)

if __name__ == "__main__":
    unittest.main()
