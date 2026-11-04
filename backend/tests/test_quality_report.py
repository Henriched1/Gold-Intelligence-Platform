import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.data import historical_store as store
from app.data.quality_report import (
    _compare_sources,
    _detect_anomalies,
    generate_data_quality_report,
)


def make_candles(count=3, unusual_index=None):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []

    for index in range(count):
        close = 2600.0 + index

        if index == unusual_index:
            close += 100.0

        rows.append({
            "timestamp": (
                start + timedelta(minutes=15 * index)
            ).isoformat(),
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 100,
        })

    return rows


class QualityReportTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Path(self.temp_dir.name) / "quality.db"
        self.path_patch = patch.object(store, "DB_PATH", self.db)
        self.path_patch.start()
        store.initialise_historical_store()

    def tearDown(self):
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def ingest(self, rows, source="feed-a"):
        return store.ingest_candles(
            "XAUUSD",
            "M15",
            rows,
            source,
            source_version="v1",
            session_convention="UTC",
        )

    def test_empty_dataset_report(self):
        report = generate_data_quality_report()

        self.assertEqual(report["status"], "NO_DATA")
        self.assertEqual(
            report["coverage"]["candle_count"], 0
        )
        self.assertTrue(report["read_only"])

    def test_volume_quality_and_coverage(self):
        rows = make_candles()
        del rows[0]["volume"]
        self.ingest(rows)

        report = generate_data_quality_report()

        self.assertEqual(
            report["volume_quality"]["available"], 2
        )
        self.assertEqual(
            report["volume_quality"]["unavailable"], 1
        )
        self.assertEqual(
            report["coverage"]["candle_count"], 3
        )
        self.assertEqual(
            report["coverage"]["gap_analysis"],
            "NOT_CLASSIFIED_SESSION_CALENDAR_REQUIRED",
        )

    def test_source_disagreements_are_reported(self):
        feed_a = make_candles()
        feed_b = make_candles()

        # Introduce a valid OHLC difference at one shared timestamp.
        feed_b[0]["open"] += 20
        feed_b[0]["high"] += 20
        feed_b[0]["low"] += 20
        feed_b[0]["close"] += 20

        self.ingest(feed_a, source="feed-a")
        self.ingest(feed_b, source="feed-b")

        report = generate_data_quality_report()

        self.assertEqual(
            report["source_comparison"]["overlapping_source_pairs"],
            3,
        )
        self.assertEqual(
            report["source_comparison"]["disagreement_count"],
            1,
        )
        self.assertEqual(
            len(report["source_comparison"]["disagreements"]),
            1,
        )
        self.assertEqual(
            len(store.get_historical_candles("XAUUSD", "M15")),
            6,
        )

    def test_invalid_ohlc_rows_are_skipped_in_source_comparison(self):
        timestamp = make_candles(1)[0]["timestamp"]
        invalid = {
            "timestamp": timestamp,
            "source": "corrupt-feed",
            "open": 2600,
            "high": 2590,
            "low": 2580,
            "close": 2585,
        }
        valid = {
            "timestamp": timestamp,
            "source": "valid-feed",
            "open": 2600,
            "high": 2610,
            "low": 2590,
            "close": 2605,
        }

        result = _compare_sources([invalid, valid], 0.0005)

        self.assertEqual(result["overlapping_source_pairs"], 0)
        self.assertEqual(result["disagreement_count"], 0)

    def test_anomaly_baselines_are_separate_per_source(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = []

        for source, base, increment in (
            ("feed-a", 100.0, 0.1),
            ("feed-b", 1000.0, 10.0),
        ):
            close = base

            for index in range(25):
                if index == 12 and source == "feed-a":
                    close += 10.0
                else:
                    close += increment

                rows.append({
                    "timestamp": (
                        start + timedelta(minutes=15 * index)
                    ).isoformat(),
                    "source": source,
                    "open": close,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                })

        report = _detect_anomalies(rows, 8.0)

        self.assertEqual(
            report["status_by_source"]["feed-a"]["close_to_close_return"],
            "ANALYSED",
        )
        self.assertEqual(
            report["status_by_source"]["feed-b"]["close_to_close_return"],
            "ANALYSED",
        )

        feed_a_anomalies = [
            item for item in report["anomalies"]
            if item["source"] == "feed-a"
            and item["metric"] == "close_to_close_return"
        ]

        self.assertTrue(feed_a_anomalies)
        feed_a_rows = sorted(
            [row for row in rows if row["source"] == "feed-a"],
            key=lambda row: row["timestamp"],
        )
        feed_a_returns = [
            (float(current["close"]) - float(previous["close"]))
            / float(previous["close"])
            for previous, current in zip(
                feed_a_rows,
                feed_a_rows[1:],
            )
        ]

        from statistics import median

        self.assertAlmostEqual(
            feed_a_anomalies[0]["median"],
            median(feed_a_returns),
            places=12,
        )

    def test_insufficient_anomaly_data_requires_review(self):
        self.ingest(make_candles(5))

        report = generate_data_quality_report()

        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertIn(
            "INSUFFICIENT_DATA",
            report["anomaly_detection"]["status_by_metric"].values(),
        )

    def test_invalid_threshold_rejected(self):
        with self.assertRaisesRegex(ValueError, "anomaly_threshold"):
            generate_data_quality_report(anomaly_threshold=0)

        with self.assertRaisesRegex(ValueError, "source_tolerance"):
            generate_data_quality_report(source_tolerance=1)

    def test_report_calculates_age_without_claiming_freshness(self):
        self.ingest(make_candles())
        report = generate_data_quality_report(
            as_of_utc="2026-01-01T01:30:00+00:00"
        )

        freshness = report["freshness_assessment"]
        self.assertEqual(freshness["classification"], "AGE_ONLY_NOT_CLASSIFIED")
        self.assertEqual(freshness["newest_candle_age_seconds"], 3600.0)
        self.assertFalse(freshness["session_calendar_verified"])
        self.assertFalse(report["quality_controls"]["signal_eligible"])

    def test_future_candles_are_flagged(self):
        self.ingest(make_candles())
        report = generate_data_quality_report(
            as_of_utc="2026-01-01T00:15:00Z"
        )

        freshness = report["freshness_assessment"]
        self.assertEqual(freshness["future_observation_rows"], 1)
        self.assertEqual(freshness["newest_candle_age_seconds"], -900.0)
        self.assertFalse(report["quality_controls"]["signal_eligible"])

    def test_report_does_not_modify_stored_records(self):
        self.ingest(make_candles(25))

        before = store.get_historical_candles("XAUUSD", "M15")
        report = generate_data_quality_report()
        after = store.get_historical_candles("XAUUSD", "M15")

        self.assertTrue(report["read_only"])
        self.assertEqual(before, after)

    def test_anomaly_analysis_requires_enough_observations(self):
        self.ingest(make_candles(5))

        report = generate_data_quality_report()

        self.assertEqual(
            report["anomaly_detection"]["status_by_metric"][
                "close_to_close_return"
            ],
            "INSUFFICIENT_DATA",
        )


if __name__ == "__main__":
    unittest.main()
