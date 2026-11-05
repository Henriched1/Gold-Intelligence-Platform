from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.api import global_coverage as api


TIMEFRAMES = ("D1", "H4", "H1", "M15")


def provider_record():
    return {
        "provider_id": "sample_feed",
        "lifecycle_state": "ACTIVE",
        "supported_instruments": [
            item["symbol"] for item in api.list_instruments()
        ] if hasattr(api, "list_instruments") else [
            item["symbol"] for item in __import__(
                "app.core.universe", fromlist=["list_instruments"]
            ).list_instruments()
        ],
        "historical_timeframes": list(TIMEFRAMES),
    }


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(
        api.provider_onboarding,
        "get_provider",
        lambda provider_id: provider_record(),
    )
    monkeypatch.setattr(
        api.provider_job_submission,
        "_approval_path",
        lambda provider_id: "/tmp/sample-approval.json",
    )
    monkeypatch.setattr(
        api.provider_approval,
        "load_approval",
        lambda path: {"provider_id": "sample_feed"},
    )

    validate = Mock()
    monkeypatch.setattr(
        api.provider_approval, "validate_approval", validate
    )

    retrieval = Mock(return_value=[
        {"timestamp": "2026-10-01T00:00:00+00:00"}
        for _ in range(8)
    ])
    monkeypatch.setattr(
        api.historical_store,
        "get_historical_candles",
        retrieval,
    )

    return {"validate": validate, "retrieval": retrieval}


def test_global_coverage_route_is_registered():
    from app.main import app
    assert "/v1/global/coverage" in app.openapi()["paths"]


def test_all_instruments_can_be_assessed_without_claiming_live_data(
    configured,
):
    result = api.global_market_coverage("sample_feed")

    assert result["universe_size"] == 30
    assert result["totals"]["research_ready"] == 30
    assert result["totals"]["blocked"] == 0
    assert len(result["instruments"]) == 30
    assert configured["validate"].call_count == 30 * 4
    assert configured["retrieval"].call_count == 30 * 4

    assert result["coverage_type"] == "STORED_HISTORICAL_DATA"
    assert result["freshness_verified"] is False
    assert result["live_data_connected"] is False
    assert result["signal_eligible"] is False
    assert result["alerts_emitted"] is False
    assert result["execution_enabled"] is False


def test_missing_licence_blocks_all_history_lookups(
    configured, monkeypatch,
):
    from app.data.provider_contract import ProviderNotApproved

    def reject(*args, **kwargs):
        raise ProviderNotApproved("approval missing")

    monkeypatch.setattr(
        api.provider_approval, "validate_approval", reject
    )

    result = api.global_market_coverage("sample_feed")

    assert result["totals"]["research_ready"] == 0
    assert result["totals"]["blocked"] == 30
    assert configured["retrieval"].call_count == 0
    assert all(
        item["status"] == "NOT_READY"
        for item in result["instruments"]
    )
    assert all(
        tf["status"] == "CURRENT_WRITTEN_DATA_LICENCE_REQUIRED"
        for item in result["instruments"]
        for tf in item["timeframes"].values()
    )


def test_insufficient_history_is_not_marked_ready(
    configured,
):
    configured["retrieval"].return_value = [
        {"timestamp": "2026-10-01T00:00:00+00:00"}
        for _ in range(7)
    ]

    result = api.global_market_coverage("sample_feed")

    assert result["totals"]["research_ready"] == 0
    assert result["totals"]["blocked"] == 30
    assert result["totals"]["insufficient_history"] == 30
    assert all(
        tf["status"] == "INSUFFICIENT_STORED_HISTORY"
        for item in result["instruments"]
        for tf in item["timeframes"].values()
    )


def test_unknown_provider_is_rejected(configured, monkeypatch):
    monkeypatch.setattr(
        api.provider_onboarding, "get_provider",
        lambda provider_id: None,
    )

    with pytest.raises(HTTPException) as error:
        api.global_market_coverage("missing_feed")

    assert error.value.status_code == 404
    assert error.value.detail["code"] == "PROVIDER_NOT_REGISTERED"
    configured["retrieval"].assert_not_called()


def test_inactive_provider_is_reported_as_blocked(
    configured, monkeypatch,
):
    provider = provider_record()
    provider["lifecycle_state"] = "DISABLED"
    monkeypatch.setattr(
        api.provider_onboarding, "get_provider",
        lambda provider_id: provider,
    )

    result = api.global_market_coverage("sample_feed")

    assert result["totals"]["research_ready"] == 0
    assert result["totals"]["blocked"] == 30
    assert configured["retrieval"].call_count == 0
    assert all(
        tf["status"] == "PROVIDER_NOT_ACTIVE"
        for item in result["instruments"]
        for tf in item["timeframes"].values()
    )
