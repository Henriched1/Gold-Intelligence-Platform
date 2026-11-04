import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.data.acquisition_plan import build_acquisition_plan
from app.data.pilot_store import (
    commit_chunk,
    initialise_pilot_database,
    register_plan,
)
from app.data.pilot_quality import generate_pilot_quality_report
from app.data.provider_contract import ProviderMetadata


class PilotQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "pilot.sqlite3"
        initialise_pilot_database(self.db)
        self.metadata = ProviderMetadata(
            provider_id="twelve_data",
            provider_version="test-v1",
            instrument_mapping={"XAUUSD": "XAU/USD"},
            supported_timeframes=frozenset({"M15", "D1"}),
            session_convention="UTC test session",
            price_component="UNKNOWN",
            volume_semantics="Test fixture",
            licensing_status="APPROVED",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def make_candle(self, timestamp):
        return {
            "timestamp": timestamp,
            "open": 4000.0,
            "high": 4002.0,
            "low": 3999.0,
            "close": 4001.0,
            "volume": 0.0,
            "volume_available": False,
        }

    def test_report_counts_only_candles_owned_by_plan(self):
        first = build_acquisition_plan(
            "XAUUSD", "M15",
            "2026-11-02T00:00:00Z",
            "2026-11-02T00:00:00Z",
            max_rows=1,
        )
        second = dict(first)
        second["plan_id"] = first["plan_id"] + "-other"

        register_plan(self.db, first)
        register_plan(self.db, second)

        candle = self.make_candle("2026-11-02T00:00:00+00:00")
        commit_chunk(self.db, second, 1, [candle], self.metadata)

        report = generate_pilot_quality_report(self.db, first["plan_id"])
        self.assertEqual(
            report["candle_summary"]["observed_rows_in_requested_range"], 0
        )

    def test_report_counts_repeated_timestamps_across_sources(self):
        plan = build_acquisition_plan(
            "XAUUSD", "M15",
            "2026-11-02T00:00:00Z",
            "2026-11-02T00:00:00Z",
            max_rows=1,
        )
        register_plan(self.db, plan)

        candle = self.make_candle("2026-11-02T00:00:00+00:00")
        commit_chunk(self.db, plan, 1, [candle], self.metadata)

        other_source = ProviderMetadata(
            provider_id="other_test_source",
            provider_version="test-v1",
            instrument_mapping={"XAUUSD": "XAU/USD"},
            supported_timeframes=frozenset({"M15"}),
            session_convention="UTC test session",
            price_component="UNKNOWN",
            volume_semantics="Test fixture",
            licensing_status="APPROVED",
        )
        commit_chunk(self.db, plan, 1, [candle], other_source)

        report = generate_pilot_quality_report(self.db, plan["plan_id"])
        self.assertEqual(report["candle_summary"]["unique_observed_timestamps"], 1)
        self.assertEqual(report["candle_summary"]["duplicate_timestamp_rows"], 1)

    def test_report_exposes_candle_age_without_claiming_freshness(self):
        plan = build_acquisition_plan(
            "XAUUSD", "M15",
            "2026-11-02T00:00:00Z",
            "2026-11-02T00:00:00Z",
            max_rows=1,
        )
        register_plan(self.db, plan)
        candle = self.make_candle("2026-11-02T00:00:00+00:00")
        commit_chunk(self.db, plan, 1, [candle], self.metadata)

        report = generate_pilot_quality_report(
            self.db,
            plan["plan_id"],
            as_of_utc="2026-11-02T01:00:00Z",
        )

        self.assertEqual(
            report["candle_summary"]["newest_candle_age_seconds"], 3600
        )
        self.assertEqual(
            report["freshness_assessment"]["classification"],
            "AGE_ONLY_NOT_CLASSIFIED",
        )
        self.assertFalse(report["quality_controls"]["signal_eligible"])

    def test_report_flags_future_observation(self):
        plan = build_acquisition_plan(
            "XAUUSD", "M15",
            "2026-11-02T00:00:00Z",
            "2026-11-02T00:00:00Z",
            max_rows=1,
        )
        register_plan(self.db, plan)
        candle = self.make_candle("2026-11-02T00:00:00+00:00")
        commit_chunk(self.db, plan, 1, [candle], self.metadata)

        report = generate_pilot_quality_report(
            self.db,
            plan["plan_id"],
            as_of_utc="2026-11-01T23:00:00Z",
        )

        self.assertEqual(
            report["candle_summary"]["future_observation_rows"], 1
        )
        self.assertEqual(
            report["freshness_assessment"]["classification"],
            "AGE_ONLY_NOT_CLASSIFIED",
        )

    def test_daily_report_uses_expanded_exchange_day_boundaries(self):
        plan = build_acquisition_plan(
            "XAUUSD", "D1",
            "2026-11-02T12:00:00Z",
            "2026-11-03T12:00:00Z",
            max_rows=10,
            exchange_timezone="Asia/Tokyo",
        )
        register_plan(self.db, plan)

        expanded_start = datetime.fromisoformat(
            plan["chunks"][0]["start"]
        ).astimezone(timezone.utc).isoformat()

        commit_chunk(
            self.db, plan, 1,
            [self.make_candle(expanded_start)],
            self.metadata,
        )

        report = generate_pilot_quality_report(self.db, plan["plan_id"])
        self.assertEqual(report["candle_summary"]["observed_rows_in_requested_range"], 1)


if __name__ == "__main__":
    unittest.main()
