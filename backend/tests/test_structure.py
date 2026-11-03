import unittest

from app.engines.structure import analyse_structure


class StructureTests(unittest.TestCase):

    def make_candles(self):
        candles = []

        for i in range(11):
            candles.append({
                "timestamp": str(i),
                "open": 100,
                "high": 105,
                "low": 95,
                "close": 101
            })

        return candles

    def test_buy_side_sweep(self):
        candles = self.make_candles()

        candles.append({
            "timestamp": "12",
            "open": 102,
            "high": 106,
            "low": 99,
            "close": 104
        })

        result = analyse_structure(candles)

        self.assertIn(
            "POTENTIAL_BUY_SIDE_LIQUIDITY_SWEEP",
            result["observations"]
        )

    def test_sell_side_sweep(self):
        candles = self.make_candles()

        candles.append({
            "timestamp": "12",
            "open": 98,
            "high": 101,
            "low": 94,
            "close": 96
        })

        result = analyse_structure(candles)

        self.assertIn(
            "POTENTIAL_SELL_SIDE_LIQUIDITY_SWEEP",
            result["observations"]
        )

    def test_insufficient_data(self):
        result = analyse_structure(self.make_candles()[:5])

        self.assertEqual(result["status"], "INSUFFICIENT_DATA")


if __name__ == "__main__":
    unittest.main()
