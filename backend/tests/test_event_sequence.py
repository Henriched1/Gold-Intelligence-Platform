import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from app.engines.event_sequence import (
    analyse_event_sequence,
    _find_fvg,
    _find_retracement,
    _select_latest_htf_candidate,
    _find_structure_shift,
    _find_displacement,
    _analyse_direction,
    _event_age,
    _is_fresh,
    _find_invalidation,
)
from app.engines.multi_timeframe import find_confirmed_swings


def make_candles():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []

    for i in range(30):
        price = 2400 + (i % 5)
        candles.append({
            "timestamp": (
                start + timedelta(minutes=15 * i)
            ).isoformat(),
            "open": price,
            "high": price + 2,
            "low": price - 2,
            "close": price + 0.5,
            "volume": 100,
        })

    return candles


def make_data():
    candles = make_candles()
    return {
        "D1": candles,
        "H4": candles,
        "H1": candles,
        "M15": candles,
    }


def candle(index, high, low):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {
        "timestamp": (
            start + timedelta(minutes=15 * index)
        ).isoformat(),
        "open": (high + low) / 2,
        "high": high,
        "low": low,
        "close": (high + low) / 2,
        "volume": 100,
    }


class EventSequenceTests(unittest.TestCase):

    def test_event_age_is_measured_in_remaining_bars(self):
        candles = make_candles()
        self.assertEqual(_event_age(candles, {"index": 10}), 19)

    def test_event_freshness_accepts_boundary(self):
        candles = make_candles()
        self.assertTrue(
            _is_fresh(candles, {"index": 10}, "H1")
        )

    def test_event_freshness_rejects_old_event(self):
        candles = make_candles()
        self.assertFalse(
            _is_fresh(candles, {"index": 0}, "H1")
        )

    def test_invalid_event_index_is_rejected(self):
        with self.assertRaises(ValueError):
            _event_age(make_candles(), {"index": 100})

    def test_bullish_sweep_invalidates_on_later_low_breach(self):
        candles = make_candles()
        sweep = {"index": 10, "sweep_extreme": 2390.0}
        candles[11]["low"] = 2389.0

        result = _find_invalidation(candles, sweep, "BULLISH")

        self.assertIsNotNone(result)
        self.assertEqual(result["index"], 11)
        self.assertEqual(result["invalidation_level"], 2390.0)
        self.assertEqual(result["event"], "BULLISH_SWEEP_LOW_BREACHED")

    def test_bearish_sweep_invalidates_on_later_high_breach(self):
        candles = make_candles()
        sweep = {"index": 10, "sweep_extreme": 2410.0}
        candles[12]["high"] = 2411.0

        result = _find_invalidation(candles, sweep, "BEARISH")

        self.assertIsNotNone(result)
        self.assertEqual(result["index"], 12)
        self.assertEqual(result["invalidation_level"], 2410.0)
        self.assertEqual(result["event"], "BEARISH_SWEEP_HIGH_BREACHED")

    def test_candles_before_sweep_cannot_invalidate_setup(self):
        candles = make_candles()
        sweep = {"index": 10, "sweep_extreme": 2390.0}
        candles[9]["low"] = 2380.0

        result = _find_invalidation(candles, sweep, "BULLISH")

        self.assertIsNone(result)


    def test_htf_invalidation_stops_sequence_progression(self):
        data = {
            timeframe: make_candles()
            for timeframe in ("D1", "H4", "H1", "M15")
        }
        data["D1"][11]["low"] = 2396.0

        sweep = {
            "index": 10,
            "timestamp": data["D1"][10]["timestamp"],
            "sweep_extreme": 2400.0,
            "event": "SELL_SIDE_LIQUIDITY_SWEEP",
        }

        def fake_sweeps(candles, direction):
            return [sweep] if candles is data["D1"] else []

        with patch(
            "app.engines.event_sequence._find_sweeps",
            side_effect=fake_sweeps,
        ), patch(
            "app.engines.event_sequence._find_displacement",
            return_value=None,
        ), patch(
            "app.engines.event_sequence._find_structure_shift",
        ) as shift_mock:
            result = _analyse_direction(data, "BULLISH")

        self.assertEqual(
            result["status"],
            "INVALIDATED_HTF_LIQUIDITY_SWEEP",
        )
        self.assertIn("htf_invalidation", result["stages"])
        shift_mock.assert_not_called()

    def test_h1_invalidation_stops_before_m15_structure_shift(self):
        data = {
            timeframe: make_candles()
            for timeframe in ("D1", "H4", "H1", "M15")
        }
        data["H1"][13]["low"] = 2389.0

        htf_sweep = {
            "index": 10,
            "timestamp": data["D1"][10]["timestamp"],
            "sweep_extreme": 2390.0,
            "event": "SELL_SIDE_LIQUIDITY_SWEEP",
        }
        h1_sweep = {
            "index": 12,
            "timestamp": data["H1"][12]["timestamp"],
            "sweep_extreme": 2390.0,
            "event": "SELL_SIDE_LIQUIDITY_SWEEP",
        }
        displacement = {
            "index": 11,
            "timestamp": data["D1"][11]["timestamp"],
            "event": "BULLISH_DISPLACEMENT",
        }

        def fake_sweeps(candles, direction):
            if candles is data["D1"]:
                return [htf_sweep]
            if candles is data["H1"]:
                return [h1_sweep]
            return []

        with patch(
            "app.engines.event_sequence._find_sweeps",
            side_effect=fake_sweeps,
        ), patch(
            "app.engines.event_sequence._find_displacement",
            return_value=displacement,
        ), patch(
            "app.engines.event_sequence._find_structure_shift",
        ) as shift_mock:
            result = _analyse_direction(data, "BULLISH")

        self.assertEqual(
            result["status"],
            "INVALIDATED_H1_LIQUIDITY_SWEEP",
        )
        self.assertIn("h1_invalidation", result["stages"])
        shift_mock.assert_not_called()

    def test_newer_htf_sweep_supersedes_older_completed_candidate(self):
        candidates = [
            {
                "timeframe": "D1",
                "sweep": {"timestamp": "2026-01-01T00:00:00+00:00"},
                "displacement": {"event": "BULLISH_DISPLACEMENT"},
            },
            {
                "timeframe": "H4",
                "sweep": {"timestamp": "2026-01-02T00:00:00+00:00"},
                "displacement": None,
            },
        ]

        selected = _select_latest_htf_candidate(candidates)

        self.assertEqual(selected["timeframe"], "H4")
        self.assertIsNone(selected["displacement"])

    def test_htf_candidate_selection_normalises_timezone(self):
        candidates = [
            {
                "timeframe": "D1",
                "sweep": {"timestamp": "2026-01-02T00:00:00+00:00"},
                "displacement": None,
            },
            {
                "timeframe": "H4",
                "sweep": {"timestamp": "2026-01-02T01:00:00+02:00"},
                "displacement": None,
            },
        ]

        selected = _select_latest_htf_candidate(candidates)

        self.assertEqual(selected["timeframe"], "D1")


    def test_htf_displacement_must_precede_h1_sweep(self):
        data = make_data()

        htf_sweep = {
            "index": 10,
            "timestamp": "2026-01-01T10:00:00+00:00",
            "event": "SELL_SIDE_LIQUIDITY_SWEEP",
        }

        early_h1_sweep = {
            "index": 10,
            "timestamp": "2026-01-01T10:15:00+00:00",
            "event": "SELL_SIDE_LIQUIDITY_SWEEP",
        }

        displacement = {
            "index": 11,
            "timestamp": "2026-01-01T10:30:00+00:00",
            "event": "BULLISH_DISPLACEMENT",
        }

        def fake_sweeps(candles, direction):
            if candles is data["D1"]:
                return [htf_sweep]
            if candles is data["H1"]:
                return [early_h1_sweep]
            return []

        with patch(
            "app.engines.event_sequence._find_sweeps",
            side_effect=fake_sweeps
        ), patch(
            "app.engines.event_sequence._find_displacement",
            return_value=displacement
        ):
            result = _analyse_direction(data, "BULLISH")

        self.assertEqual(
            result["status"],
            "WAITING_FOR_H1_LIQUIDITY_SWEEP"
        )
        self.assertNotIn("h1_sweep", result["stages"])

    def test_m15_structure_shift_must_follow_h1_sweep(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)

        values = [
            (100, 101, 99, 100.5),
            (100, 103, 99, 100.5),
            (100, 110, 98, 101),
            (100, 104, 99, 100.5),
            (100, 103, 100, 101),
            (101, 105, 100, 102),
            (105, 112, 104, 111),
            (106, 108, 103, 107),
        ]

        candles = []
        for i, (opening, high, low, close) in enumerate(values):
            candles.append({
                "timestamp": (
                    start + timedelta(minutes=15 * i)
                ).isoformat(),
                "open": opening,
                "high": high,
                "low": low,
                "close": close,
                "volume": 100,
            })

        before_shift = _find_structure_shift(
            candles,
            datetime.fromisoformat(candles[5]["timestamp"]),
            "BULLISH",
        )

        after_shift = _find_structure_shift(
            candles,
            datetime.fromisoformat(candles[6]["timestamp"]),
            "BULLISH",
        )

        self.assertIsNotNone(before_shift)
        self.assertEqual(before_shift["index"], 6)
        self.assertIsNone(after_shift)

    def test_displacement_cannot_use_sweep_candle(self):
        candles = []
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)

        for i in range(8):
            opening = 100
            close = 100.5
            high = 101
            low = 99

            if i == 6:
                close = 102
                high = 102.5
                low = 99.5

            candles.append({
                "timestamp": (
                    start + timedelta(minutes=15 * i)
                ).isoformat(),
                "open": opening,
                "high": high,
                "low": low,
                "close": close,
                "volume": 100,
            })

        after_sweep = _find_displacement(
            candles, {"index": 5}, "BULLISH"
        )
        same_candle = _find_displacement(
            candles, {"index": 6}, "BULLISH"
        )

        self.assertIsNotNone(after_sweep)
        self.assertEqual(after_sweep["index"], 6)
        self.assertIsNone(same_candle)

    def test_incomplete_sequence_does_not_create_signal(self):
        result = analyse_event_sequence(make_data())

        self.assertEqual(
            result["overall_status"],
            "NO_COMPLETED_SEQUENCE"
        )
        self.assertIsNone(result["probability"])
        self.assertIsNone(result["trade_signal"])

    def test_missing_timeframe_rejected(self):
        with self.assertRaises(ValueError):
            analyse_event_sequence({"D1": make_candles()})

    def test_timezone_naive_timestamps_rejected(self):
        data = make_data()

        for candles in data.values():
            for item in candles:
                item["timestamp"] = (
                    item["timestamp"].replace("+00:00", "")
                )

        with self.assertRaises(ValueError):
            analyse_event_sequence(data)

    def test_fvg_cannot_use_structure_shift_candle(self):
        candles = [
            candle(0, 102, 98),
            candle(1, 103, 101),
            candle(2, 102, 100),
            candle(3, 106, 105),
        ]

        result = _find_fvg(candles, 1, "BULLISH")
        self.assertIsNone(result)

    def test_all_fvg_formation_candles_follow_structure_shift(self):
        candles = [
            candle(0, 102, 98),
            candle(1, 103, 101),
            candle(2, 102, 100),
            candle(3, 104, 102),
            candle(4, 106, 105),
        ]

        result = _find_fvg(candles, 1, "BULLISH")

        self.assertIsNotNone(result)
        self.assertEqual(result["index"], 4)
        self.assertEqual(result["event"], "BULLISH_FVG_CANDIDATE")
        self.assertGreater(result["index"] - 2, 1)

    def test_retracement_must_happen_after_fvg(self):
        candles = [
            candle(0, 102, 98),
            candle(1, 101, 99),
            candle(2, 102, 100),
            candle(3, 103, 101),
            candle(4, 106, 105),
            candle(5, 104, 102),
        ]

        fvg = {
            "index": 4,
            "lower": 102,
            "upper": 105,
        }

        result = _find_retracement(
            candles, fvg, "BULLISH"
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["index"], 5)

    def test_unconfirmed_end_pivot_is_not_returned(self):
        candles = [
            candle(0, 100, 90),
            candle(1, 102, 91),
            candle(2, 110, 89),
            candle(3, 103, 92),
            candle(4, 101, 93),
            candle(5, 120, 94),
        ]

        highs, lows = find_confirmed_swings(candles)

        self.assertNotIn(
            5,
            [pivot["index"] for pivot in highs]
        )


if __name__ == "__main__":
    unittest.main()
