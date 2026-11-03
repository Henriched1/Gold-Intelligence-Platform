from contextlib import closing
import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app.data.acquisition_plan import build_acquisition_plan
from app.data.acquisition_runner import run_acquisition_plan
from app.data.pilot_store import commit_chunk, get_chunk_statuses, register_plan
from app.data.provider_contract import ProviderMetadata, ProviderNotApproved
from app.data.historical_store import DB_PATH as PRODUCTION_DB_PATH


class FakeProvider:
    def __init__(self, observations=None):
        self.metadata = ProviderMetadata(
            provider_id="twelve_data", provider_version="fake-v1",
            instrument_mapping={"XAUUSD": "XAU/USD"},
            supported_timeframes=frozenset({"M15"}),
            session_convention="UTC test session",
            price_component="UNKNOWN",
            volume_semantics="Test fixture",
            licensing_status="APPROVED",
        )
        self.observations = observations or [{
            "timestamp": "2026-11-02T00:00:00+00:00",
            "open": 4000, "high": 4002, "low": 3999, "close": 4001,
        }]
        self.calls = 0

    def fetch_candles(self, request):
        self.calls += 1
        return self.observations


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "pilot.sqlite3"
        self.approval = self.root / "approval.json"
        self.plan = build_acquisition_plan(
            "XAUUSD", "M15",
            "2026-11-02T00:00:00Z", "2026-11-02T00:00:00Z",
            max_rows=1,
        )
        today = date.today()
        record = {
            "provider_id": "twelve_data", "status": "APPROVED",
            "approval_reference": "TEST-ONLY-NOT-REAL-LICENCE",
            "approved_at": (today - timedelta(days=1)).isoformat(),
            "expires_at": (today + timedelta(days=30)).isoformat(),
            "commercial_use": True,
            "non_us_market_data_approval": True,
            "historical_storage": True, "derived_data_storage": True,
            "derived_data_display": True, "raw_data_redistribution": False,
            "permitted_instruments": ["XAUUSD"],
            "permitted_timeframes": ["M15"],
            "exchange_timezone": "UTC", "retention_days": 365,
            "attribution_required": False,
        }
        self.approval.write_text(json.dumps(record))

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_never_calls_provider_or_writes_candles(self):
        provider = FakeProvider()
        result = run_acquisition_plan(provider, self.plan, self.db)
        self.assertEqual(result["status"], "DRY_RUN")
        self.assertEqual(result["network_requests_made"], 0)
        self.assertFalse(result["market_data_written"])
        self.assertEqual(provider.calls, 0)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM pilot_candles").fetchone()[0], 0)

    def test_missing_approval_blocks_provider(self):
        provider = FakeProvider()
        with self.assertRaises(ProviderNotApproved):
            run_acquisition_plan(
                provider, self.plan, self.db,
                approval_path=self.root / "missing.json", execute=True)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(
            get_chunk_statuses(self.db, self.plan["plan_id"])[0]["status"],
            "FAILED")

    def test_execution_and_resume_skip_completed_chunk(self):
        provider = FakeProvider()
        first = run_acquisition_plan(
            provider, self.plan, self.db,
            approval_path=self.approval, execute=True)
        second = run_acquisition_plan(
            provider, self.plan, self.db,
            approval_path=self.approval, execute=True, resume=True)
        self.assertEqual(first["candles_inserted"], 1)
        self.assertEqual(second["network_requests_made"], 0)
        self.assertEqual(second["skipped_completed"], 1)
        self.assertEqual(provider.calls, 1)

    def test_invalid_ohlc_fails_without_candle_write(self):
        provider = FakeProvider([{
            "timestamp": "2026-11-02T00:00:00+00:00",
            "open": 4000, "high": 3998, "low": 3999, "close": 4001,
        }])
        with self.assertRaises(Exception):
            run_acquisition_plan(
                provider, self.plan, self.db,
                approval_path=self.approval, execute=True)
        self.assertEqual(
            get_chunk_statuses(self.db, self.plan["plan_id"])[0]["status"],
            "FAILED")
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM pilot_candles").fetchone()[0], 0)

    def test_identical_candle_from_another_plan_is_duplicate(self):
        provider = FakeProvider()
        second = copy.deepcopy(self.plan)
        second["plan_id"] += "-second"
        from app.data.pilot_store import initialise_pilot_database
        initialise_pilot_database(self.db)
        register_plan(self.db, self.plan)
        register_plan(self.db, second)
        candle = {
            "timestamp": "2026-11-02T00:00:00+00:00",
            "open": 4000.0, "high": 4002.0, "low": 3999.0,
            "close": 4001.0, "volume": 0.0, "volume_available": False,
        }
        self.assertEqual(
            commit_chunk(self.db, self.plan, 1, [candle], provider.metadata),
            {"inserted": 1, "duplicates": 0})
        self.assertEqual(
            commit_chunk(self.db, second, 1, [candle], provider.metadata),
            {"inserted": 0, "duplicates": 1})

    def test_production_database_path_is_rejected(self):
        from app.data.pilot_store import initialise_pilot_database
        with self.assertRaises(ValueError):
            initialise_pilot_database(PRODUCTION_DB_PATH)


if __name__ == "__main__":
    unittest.main()
