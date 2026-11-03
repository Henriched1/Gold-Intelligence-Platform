import unittest
from datetime import datetime

from app.data.acquisition_plan import build_acquisition_plan


class AcquisitionPlanTests(unittest.TestCase):
    def test_intraday_chunks_are_bounded_and_non_overlapping(self):
        plan = build_acquisition_plan(
            "XAUUSD",
            "M15",
            "2026-01-01T00:00:00Z",
            "2026-01-01T02:15:00Z",
            max_rows=4,
        )

        self.assertEqual(plan["chunk_count"], 3)
        self.assertEqual(
            [item["bar_slots"] for item in plan["chunks"]],
            [4, 4, 2],
        )

        first_end = datetime.fromisoformat(plan["chunks"][0]["end"])
        second_start = datetime.fromisoformat(plan["chunks"][1]["start"])

        self.assertEqual(
            (second_start - first_end).total_seconds(),
            900,
        )

    def test_exact_limit_uses_one_chunk(self):
        plan = build_acquisition_plan(
            "XAUUSD",
            "H1",
            "2026-01-01T00:00:00Z",
            "2026-01-01T04:00:00Z",
            max_rows=5,
        )

        self.assertEqual(plan["chunk_count"], 1)
        self.assertEqual(plan["chunks"][0]["bar_slots"], 5)

    def test_full_provider_limit_is_supported(self):
        plan = build_acquisition_plan(
            "XAUUSD",
            "M15",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
            max_rows=5000,
        )

        self.assertEqual(plan["max_rows_per_request"], 5000)

    def test_daily_chunks_use_exchange_timezone(self):
        plan = build_acquisition_plan(
            "XAUUSD",
            "D1",
            "2026-01-01T12:00:00Z",
            "2026-01-05T12:00:00Z",
            max_rows=2,
            exchange_timezone="America/New_York",
        )

        self.assertEqual(plan["planning_timezone"], "America/New_York")
        self.assertEqual(plan["chunk_count"], 3)
        self.assertEqual(
            [item["bar_slots"] for item in plan["chunks"]],
            [2, 2, 1],
        )
        self.assertTrue(
            plan["chunks"][0]["start"].endswith("-05:00")
        )

    def test_daily_chunks_handle_daylight_saving_transition(self):
        plan = build_acquisition_plan(
            "XAUUSD",
            "D1",
            "2026-03-07T12:00:00Z",
            "2026-03-10T12:00:00Z",
            max_rows=2,
            exchange_timezone="America/New_York",
        )

        first_end = datetime.fromisoformat(plan["chunks"][0]["end"])
        second_start = datetime.fromisoformat(plan["chunks"][1]["start"])

        self.assertEqual(first_end.utcoffset().total_seconds(), -18000)
        self.assertEqual(second_start.utcoffset().total_seconds(), -14400)

    def test_plan_is_deterministic(self):
        args = (
            "XAUUSD",
            "H1",
            "2026-01-01T00:00:00Z",
            "2026-01-02T00:00:00Z",
        )

        first = build_acquisition_plan(*args, max_rows=100)
        second = build_acquisition_plan(*args, max_rows=100)

        self.assertEqual(first["plan_id"], second["plan_id"])

    def test_plan_changes_when_request_changes(self):
        first = build_acquisition_plan(
            "XAUUSD", "H1",
            "2026-01-01T00:00:00Z",
            "2026-01-02T00:00:00Z",
        )
        second = build_acquisition_plan(
            "XAUUSD", "H1",
            "2026-01-01T00:00:00Z",
            "2026-01-03T00:00:00Z",
        )

        self.assertNotEqual(first["plan_id"], second["plan_id"])

    def test_plan_never_executes_or_writes_data(self):
        plan = build_acquisition_plan(
            "XAUUSD", "M15",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:15:00Z",
        )

        self.assertFalse(plan["execution_enabled"])
        self.assertEqual(plan["network_requests_made"], 0)
        self.assertFalse(plan["market_data_written"])
        self.assertEqual(plan["status"], "PLANNED_NOT_EXECUTED")

    def test_naive_timestamps_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            build_acquisition_plan(
                "XAUUSD", "H1",
                "2026-01-01T00:00:00",
                "2026-01-02T00:00:00Z",
            )

    def test_reversed_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "after"):
            build_acquisition_plan(
                "XAUUSD", "H1",
                "2026-01-02T00:00:00Z",
                "2026-01-01T00:00:00Z",
            )

    def test_unaligned_intraday_boundary_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "align"):
            build_acquisition_plan(
                "XAUUSD", "M15",
                "2026-01-01T00:05:00Z",
                "2026-01-01T01:00:00Z",
            )

    def test_invalid_timezone_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            build_acquisition_plan(
                "XAUUSD", "D1",
                "2026-01-01T00:00:00Z",
                "2026-01-02T00:00:00Z",
                exchange_timezone="Not/A_Timezone",
            )

    def test_unsupported_market_and_timeframe_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "instrument"):
            build_acquisition_plan(
                "BTCUSD", "H1",
                "2026-01-01T00:00:00Z",
                "2026-01-02T00:00:00Z",
            )

        with self.assertRaisesRegex(ValueError, "timeframe"):
            build_acquisition_plan(
                "XAUUSD", "M5",
                "2026-01-01T00:00:00Z",
                "2026-01-02T00:00:00Z",
            )

    def test_invalid_row_limits_are_rejected(self):
        for value in (0, -1, 5001, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "max_rows"):
                    build_acquisition_plan(
                        "XAUUSD", "H1",
                        "2026-01-01T00:00:00Z",
                        "2026-01-02T00:00:00Z",
                        max_rows=value,
                    )


if __name__ == "__main__":
    unittest.main()
