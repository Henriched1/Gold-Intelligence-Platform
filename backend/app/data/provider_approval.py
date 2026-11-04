"""Fail-closed licensing approval checks for external data providers."""

import json
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.data.provider_contract import ProviderNotApproved


PROVIDER_ID = "twelve_data"

REQUIRED_RIGHTS = (
    "commercial_use",
    "non_us_market_data_approval",
    "historical_storage",
    "derived_data_storage",
    "derived_data_display",
)


def load_approval(path):
    path = Path(path)

    if not path.is_file():
        return None

    try:
        with path.open("r", encoding="utf-8") as file:
            record = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise ProviderNotApproved(
            "provider approval record cannot be read"
        ) from error

    if not isinstance(record, dict):
        raise ProviderNotApproved(
            "provider approval record must be an object"
        )

    return record


def validate_approval(
    record,
    instrument,
    timeframe,
    today=None,
    provider_id=PROVIDER_ID,
):
    if not isinstance(record, dict):
        raise ProviderNotApproved(
            "written provider approval is required"
        )

    if record.get("provider_id") != provider_id:
        raise ProviderNotApproved("approval provider does not match")

    if record.get("status") != "APPROVED":
        raise ProviderNotApproved(
            "provider licensing approval is not active"
        )

    reference = record.get("approval_reference")
    if not isinstance(reference, str) or not reference.strip():
        raise ProviderNotApproved(
            "written approval reference is required"
        )

    today = today or date.today()

    try:
        approved_at = date.fromisoformat(record["approved_at"])
        expires_at = date.fromisoformat(record["expires_at"])
    except (KeyError, TypeError, ValueError) as error:
        raise ProviderNotApproved(
            "approval dates must be valid ISO dates"
        ) from error

    if approved_at > today or expires_at <= today:
        raise ProviderNotApproved(
            "provider approval is not currently valid"
        )

    if expires_at <= approved_at:
        raise ProviderNotApproved(
            "approval expiry must follow its approval date"
        )

    for right in REQUIRED_RIGHTS:
        if record.get(right) is not True:
            raise ProviderNotApproved(
                f"required licensing permission is missing: {right}"
            )

    if record.get("raw_data_redistribution") is not False:
        raise ProviderNotApproved(
            "raw-data redistribution must remain disabled"
        )

    if instrument not in record.get("permitted_instruments", []):
        raise ProviderNotApproved(
            "instrument is outside the approved licensing scope"
        )

    if timeframe not in record.get("permitted_timeframes", []):
        raise ProviderNotApproved(
            "timeframe is outside the approved licensing scope"
        )

    timezone_name = record.get("exchange_timezone")
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ProviderNotApproved(
            "provider exchange timezone must be documented"
        )

    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ProviderNotApproved(
            "provider exchange timezone is invalid"
        ) from error

    retention_days = record.get("retention_days")
    if (
        not isinstance(retention_days, int)
        or isinstance(retention_days, bool)
        or retention_days < 1
    ):
        raise ProviderNotApproved(
            "permitted retention period must be documented"
        )

    if record.get("attribution_required") is True:
        attribution = record.get("attribution_text")
        if not isinstance(attribution, str) or not attribution.strip():
            raise ProviderNotApproved(
                "required provider attribution text is missing"
            )

    return record


LIVE_REQUIRED_RIGHTS = (
    "live_market_data_access",
    "live_data_storage",
)


def validate_live_approval(
    record,
    instrument,
    timeframe,
    today=None,
    provider_id=PROVIDER_ID,
):
    """Require historical rights plus explicit live-feed permissions."""
    approved = validate_approval(
        record,
        instrument,
        timeframe,
        today=today,
        provider_id=provider_id,
    )

    for right in LIVE_REQUIRED_RIGHTS:
        if approved.get(right) is not True:
            raise ProviderNotApproved(
                f"required live-data permission is missing: {right}"
            )

    return approved
