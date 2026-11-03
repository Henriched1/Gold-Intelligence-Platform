import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.data import historical_store as store


def candle(day, close=10.0):
    return {
        "timestamp": f"2026-01-{day:02d}T00:00:00Z",
        "open": close - 1,
        "high": close + 1,
        "low": close - 2,
        "close": close,
        "volume": 100,
    }


class HistoricalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Path(self.temp_dir.name) / "test.db"
        self.path_patch = patch.object(store, "DB_PATH", self.db)
        self.path_patch.start()
        store.initialise_historical_store()

    def tearDown(self):
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def ingest(self, candles, source="feed-a"):
        return store.ingest_candles(
            "XAUUSD",
            "H1",
            candles,
            source,
            source_version="v1",
            session_convention="UTC",
        )

    def test_ingest_and_chronological_retrieval(self):
        result = self.ingest([candle(1), candle(2)])
        self.assertEqual(result["inserted"], 2)

        rows = store.get_historical_candles("XAUUSD", "H1")
        self.assertEqual(len(rows), 2)
        self.assertLess(rows[0]["timestamp"], rows[1]["timestamp"])
        self.assertEqual(rows[0]["source_version"], "v1")
        self.assertEqual(rows[0]["volume_available"], 1)

    def test_missing_volume_is_marked_unavailable(self):
        item = candle(1)
        del item["volume"]
        self.ingest([item])
        rows = store.get_historical_candles("XAUUSD", "H1")
        self.assertEqual(rows[0]["volume"], 0.0)
        self.assertEqual(rows[0]["volume_available"], 0)

    def test_legacy_schema_migration_preserves_unknown_volume(self):
        legacy_db = Path(self.temp_dir.name) / "legacy.db"
        connection = sqlite3.connect(legacy_db)
        connection.execute("""
            CREATE TABLE historical_candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                source TEXT NOT NULL,
                source_version TEXT NOT NULL,
                session_convention TEXT NOT NULL,
                ingested_at TEXT NOT NULL,
                UNIQUE(instrument, timeframe, timestamp, source)
            )
        """)
        connection.execute("""
            INSERT INTO historical_candles (
                instrument, timeframe, timestamp,
                open, high, low, close, volume,
                source, source_version, session_convention, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "XAUUSD", "H1", "2026-01-01T00:00:00+00:00",
            99.0, 101.0, 98.0, 100.0, 0.0,
            "legacy-feed", "v1", "UTC", "2026-01-01T01:00:00+00:00"
        ))
        connection.commit()
        connection.close()

        with patch.object(store, "DB_PATH", legacy_db):
            store.initialise_historical_store()
            rows = store.get_historical_candles("XAUUSD", "H1")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 100.0)
        self.assertEqual(rows[0]["volume"], 0.0)
        self.assertIsNone(rows[0]["volume_available"])


    def test_exact_replay_is_idempotent(self):
        self.ingest([candle(1)])
        result = self.ingest([candle(1)])

        self.assertEqual(result, {
            "inserted": 0,
            "duplicates": 1,
            "total_received": 1,
        })

    def test_conflicting_duplicate_rejected(self):
        self.ingest([candle(1)])

        with self.assertRaisesRegex(ValueError, "conflicting candle"):
            self.ingest([candle(1, close=12)])

    def test_conflict_preflight_prevents_partial_insert(self):
        self.ingest([candle(2)])

        with self.assertRaises(ValueError):
            self.ingest([candle(1), candle(2, close=12)])

        self.assertEqual(
            len(store.get_historical_candles("XAUUSD", "H1")),
            1,
        )

    def test_another_source_is_preserved(self):
        self.ingest([candle(1)], source="feed-a")
        self.ingest([candle(1, close=11)], source="feed-b")

        self.assertEqual(
            len(store.get_historical_candles("XAUUSD", "H1")),
            2,
        )

    def test_naive_timestamp_rejected(self):
        item = candle(1)
        item["timestamp"] = "2026-01-01T00:00:00"

        with self.assertRaisesRegex(ValueError, "timezone"):
            self.ingest([item])

    def test_invalid_ohlc_and_infinite_volume_rejected(self):
        item = candle(1)
        item["high"] = 1

        with self.assertRaisesRegex(ValueError, "high"):
            self.ingest([item])

        item = candle(1)
        item["volume"] = float("inf")

        with self.assertRaisesRegex(ValueError, "volume"):
            self.ingest([item])

    def test_duplicate_or_unsorted_batch_rejected(self):
        with self.assertRaisesRegex(ValueError, "chronological"):
            self.ingest([candle(2), candle(1)])

        with self.assertRaisesRegex(ValueError, "chronological"):
            self.ingest([candle(1), candle(1)])

    def test_coverage_does_not_assume_session_gaps(self):
        self.ingest([candle(1), candle(2)])
        coverage = store.get_data_coverage("XAUUSD", "H1")

        self.assertEqual(coverage["candle_count"], 2)
        self.assertEqual(
            coverage["gap_analysis"],
            "NOT_CLASSIFIED_SESSION_CALENDAR_REQUIRED",
        )

    def test_append_only_triggers_block_update_and_delete(self):
        self.ingest([candle(1)])
        connection = sqlite3.connect(self.db)

        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE historical_candles SET close=99 WHERE id=1"
                )

            connection.rollback()

            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM historical_candles WHERE id=1"
                )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
