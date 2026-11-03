import unittest

from app.engines.market_structure import analyse_market_structure


class MarketStructureTests(unittest.TestCase):

    def base_candles(self):
        candles = []

        for i in range(12):
            candles.append({
                "timestamp": str(i),
                "open": 100,
                "high": 105 if i == 5 else 103,
                "low": 95 if i == 5 else 97,
                "close": 101
            })

        return candles

    def test_buy_side_sweep(self):
        candles = self.base_candles()

        candles.append({
            "timestamp": "13",
            "open": 102,
            "high": 106,
            "low": 99,
            "close": 104
        })

        result = analyse_market_structure(candles)

        self.assertIn(
            "BUY_SIDE_LIQUIDITY_SWEEP",
            result["events"]
        )

    def test_sell_side_sweep(self):
        candles = self.base_candles()

        candles.append({
            "timestamp": "13",
            "open": 100,
            "high": 101,
            "low": 94,
            "close": 96
        })

        result = analyse_market_structure(candles)

        self.assertIn(
            "SELL_SIDE_LIQUIDITY_SWEEP",
            result["events"]
        )

    def test_insufficient_data(self):
        result = analyse_market_structure(
            self.base_candles()[:5]
        )

        self.assertEqual(
            result["status"],
            "INSUFFICIENT_DATA"
        )


if __name__ == "__main__":
    unittest.main()
