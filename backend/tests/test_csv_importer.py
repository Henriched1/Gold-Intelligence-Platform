import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.data import csv_importer


class CsvImporterTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "gold.csv"

    def rows(self):
        return [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "open": "100", "high": "105",
                "low": "99", "close": "103", "volume": "10",
            },
            {
                "timestamp": "2026-01-01T01:00:00Z",
                "open": "103", "high": "106",
                "low": "102", "close": "104", "volume": "12",
            },
        ]

    def write(self, rows, headers=None):
        headers = headers or [
            "timestamp", "open", "high", "low", "close", "volume"
        ]
        with self.path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=headers, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)

    def run_import(self):
        return csv_importer.import_csv(
            self.path, "XAUUSD", "H1", "test-provider", "v1", "UTC"
        )

    @patch("app.data.csv_importer.ingest_candles")
    def test_valid_file(self, ingest):
        self.write(self.rows())
        ingest.return_value = {
            "inserted": 2, "duplicates": 0, "total_received": 2
        }
        result = self.run_import()
        self.assertEqual(result["status"], "IMPORTED")
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(
            ingest.call_args.kwargs["source"], "test-provider"
        )

    @patch("app.data.csv_importer.ingest_candles")
    def test_invalid_row_rejects_entire_file(self, ingest):
        rows = self.rows()
        rows[1]["close"] = "nan"
        self.write(rows)
        result = self.run_import()
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["rejected"], 2)
        ingest.assert_not_called()

    @patch("app.data.csv_importer.ingest_candles")
    def test_timezone_required(self, ingest):
        rows = self.rows()
        rows[0]["timestamp"] = "2026-01-01T00:00:00"
        self.write(rows)
        self.assertEqual(self.run_import()["status"], "REJECTED")
        ingest.assert_not_called()

    @patch("app.data.csv_importer.ingest_candles")
    def test_chronological_order_required(self, ingest):
        self.write(list(reversed(self.rows())))
        self.assertEqual(self.run_import()["status"], "REJECTED")
        ingest.assert_not_called()

    @patch("app.data.csv_importer.ingest_candles")
    def test_duplicate_timestamps_rejected(self, ingest):
        rows = self.rows()
        rows[1]["timestamp"] = rows[0]["timestamp"]
        self.write(rows)
        self.assertEqual(self.run_import()["status"], "REJECTED")
        ingest.assert_not_called()

    @patch("app.data.csv_importer.ingest_candles")
    def test_equivalent_timezone_timestamps_rejected(self, ingest):
        rows = self.rows()
        rows[1]["timestamp"] = "2026-01-01T01:00:00+01:00"
        self.write(rows)
        result = self.run_import()
        self.assertEqual(result["status"], "REJECTED")
        ingest.assert_not_called()

    @patch("app.data.csv_importer.ingest_candles")
    def test_required_columns(self, ingest):
        self.write(self.rows(), ["timestamp", "open", "high", "low"])
        with self.assertRaisesRegex(ValueError, "missing required"):
            self.run_import()
        ingest.assert_not_called()

    @patch("app.data.csv_importer.ingest_candles")
    def test_volume_defaults_to_zero(self, ingest):
        rows = [
            {k: v for k, v in row.items() if k != "volume"}
            for row in self.rows()
        ]
        self.write(rows, ["timestamp", "open", "high", "low", "close"])
        ingest.return_value = {
            "inserted": 2, "duplicates": 0, "total_received": 2
        }
        self.run_import()
        imported = ingest.call_args.kwargs["candles"][0]
        self.assertEqual(imported["volume"], 0.0)
        self.assertFalse(imported["volume_available"])

    @patch("app.data.csv_importer.ingest_candles")
    def test_explicit_zero_volume_is_available(self, ingest):
        rows = self.rows()
        rows[0]["volume"] = "0"
        self.write(rows)
        ingest.return_value = {
            "inserted": 2, "duplicates": 0, "total_received": 2
        }
        self.run_import()
        imported = ingest.call_args.kwargs["candles"][0]
        self.assertEqual(imported["volume"], 0.0)
        self.assertTrue(imported["volume_available"])

    @patch("app.data.csv_importer.ingest_candles")
    def test_storage_conflict_rejected(self, ingest):
        self.write(self.rows())
        ingest.side_effect = ValueError("conflicting candle")
        result = self.run_import()
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["inserted"], 0)

    @patch("app.data.csv_importer.ingest_candles")
    def test_duplicates_reported(self, ingest):
        self.write(self.rows())
        ingest.return_value = {
            "inserted": 0, "duplicates": 2, "total_received": 2
        }
        result = self.run_import()
        self.assertEqual(result["duplicates"], 2)
        self.assertEqual(result["status"], "IMPORTED")

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            csv_importer.import_csv(
                self.path, "XAUUSD", "H1", "test-provider"
            )


if __name__ == "__main__":
    unittest.main()
