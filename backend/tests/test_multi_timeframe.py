import unittest
from datetime import datetime, timedelta, timezone

from app.engines.multi_timeframe import (
    analyse_multi_timeframe,
    analyse_timeframe,
    validate_candles,
)


def sample_candles():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []

    for i in range(20):
        base = 100 + (i % 4)
        candles.append({
            "timestamp": (start + timedelta(minutes=15*i)).isoformat(),
            "open": base,
            "high": base + 2,
            "low": base - 2,
            "close": base + 0.5
        })

    return candles


class MultiTimeframeTests(unittest.TestCase):

    def test_valid_candles(self):
        validate_candles(sample_candles())

    def test_rejects_insufficient_candles(self):
        with self.assertRaises(ValueError):
            validate_candles(sample_candles()[:5])

    def test_rejects_invalid_ohlc(self):
        candles = sample_candles()
        candles[-1]["high"] = 1

        with self.assertRaises(ValueError):
            validate_candles(candles)

    def test_rejects_duplicate_timestamps(self):
        candles = sample_candles()
        candles[-1]["timestamp"] = candles[-2]["timestamp"]

        with self.assertRaises(ValueError):
            validate_candles(candles)

    def test_timeframe_analysis(self):
        result = analyse_timeframe("H1", sample_candles())
        self.assertEqual(result["timeframe"], "H1")
        self.assertIn(
            result["direction"],
            ("BULLISH", "BEARISH", "NEUTRAL")
        )

    def test_all_four_timeframes(self):
        candles = sample_candles()
        data = {
            "D1": candles,
            "H4": candles,
            "H1": candles,
            "M15": candles
        }

        result = analyse_multi_timeframe(data)

        self.assertEqual(len(result["timeframes"]), 4)
        self.assertIsNone(result["probability"])
        self.assertIsNone(result["trade_signal"])

    def test_missing_timeframe_rejected(self):
        with self.assertRaises(ValueError):
            analyse_multi_timeframe({"D1": sample_candles()})


if __name__ == "__main__":
    unittest.main()
