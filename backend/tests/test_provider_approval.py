import unittest
from copy import deepcopy
from datetime import date

from app.data.provider_approval import (
    REQUIRED_RIGHTS,
    load_approval,
    validate_approval,
)
from app.data.provider_contract import ProviderNotApproved


def approved_record():
    record = {
        "provider_id": "twelve_data",
        "status": "APPROVED",
        "approval_reference": "WRITTEN-APPROVAL-001",
        "approved_at": "2026-11-01",
        "expires_at": "2027-11-01",
        "permitted_instruments": ["XAUUSD"],
        "permitted_timeframes": ["M15", "H1", "H4", "D1"],
        "exchange_timezone": "UTC",
        "retention_days": 365,
        "commercial_use": True,
        "non_us_market_data_approval": True,
        "historical_storage": True,
        "derived_data_storage": True,
        "derived_data_display": True,
        "raw_data_redistribution": False,
        "attribution_required": True,
        "attribution_text": "Data provided by Twelve Data",
    }
    return record


class ProviderApprovalTests(unittest.TestCase):
    TODAY = date(2026, 11, 3)

    def validate(self, record, instrument="XAUUSD", timeframe="M15"):
        return validate_approval(
            record,
            instrument,
            timeframe,
            today=self.TODAY,
        )

    def test_missing_approval_is_rejected(self):
        with self.assertRaises(ProviderNotApproved):
            self.validate(None)

    def test_example_not_approved_record_is_rejected(self):
        record = approved_record()
        record["status"] = "NOT_APPROVED"

        with self.assertRaises(ProviderNotApproved):
            self.validate(record)

    def test_valid_approval_is_accepted(self):
        result = self.validate(approved_record())
        self.assertEqual(result["provider_id"], "twelve_data")

    def test_missing_approval_reference_is_rejected(self):
        record = approved_record()
        record["approval_reference"] = ""

        with self.assertRaises(ProviderNotApproved):
            self.validate(record)

    def test_expired_approval_is_rejected(self):
        record = approved_record()
        record["expires_at"] = "2026-11-03"

        with self.assertRaises(ProviderNotApproved):
            self.validate(record)

    def test_future_approval_date_is_rejected(self):
        record = approved_record()
        record["approved_at"] = "2026-11-04"

        with self.assertRaises(ProviderNotApproved):
            self.validate(record)

    def test_every_required_permission_is_enforced(self):
        for right in REQUIRED_RIGHTS:
            with self.subTest(right=right):
                record = approved_record()
                record[right] = False

                with self.assertRaises(ProviderNotApproved):
                    self.validate(record)

    def test_raw_data_redistribution_must_remain_disabled(self):
        record = approved_record()
        record["raw_data_redistribution"] = True

        with self.assertRaises(ProviderNotApproved):
            self.validate(record)

    def test_instrument_scope_is_enforced(self):
        with self.assertRaises(ProviderNotApproved):
            self.validate(approved_record(), instrument="XAGUSD")

    def test_timeframe_scope_is_enforced(self):
        with self.assertRaises(ProviderNotApproved):
            self.validate(approved_record(), timeframe="M5")

    def test_invalid_timezone_is_rejected(self):
        record = approved_record()
        record["exchange_timezone"] = "Not/A_Real_Timezone"

        with self.assertRaises(ProviderNotApproved):
            self.validate(record)

    def test_retention_must_be_a_positive_integer(self):
        for value in (0, -1, True, "365"):
            with self.subTest(value=value):
                record = approved_record()
                record["retention_days"] = value

                with self.assertRaises(ProviderNotApproved):
                    self.validate(record)

    def test_required_attribution_must_be_documented(self):
        record = approved_record()
        record["attribution_text"] = ""

        with self.assertRaises(ProviderNotApproved):
            self.validate(record)

    def test_attribution_text_not_required_when_not_required(self):
        record = approved_record()
        record["attribution_required"] = False
        record["attribution_text"] = ""

        self.assertEqual(
            self.validate(record)["provider_id"],
            "twelve_data",
        )

    def test_wrong_provider_is_rejected(self):
        record = approved_record()
        record["provider_id"] = "another_provider"

        with self.assertRaises(ProviderNotApproved):
            self.validate(record)

    def test_missing_private_approval_file_returns_none(self):
        self.assertIsNone(
            load_approval("config/provider_approvals.json")
        )


if __name__ == "__main__":
    unittest.main()
