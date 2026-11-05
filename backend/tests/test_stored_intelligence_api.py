from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.api import stored_intelligence as api

TIMEFRAMES = ("D1", "H4", "H1", "M15")


def provider_record(lifecycle="ACTIVE"):
    return {
        "provider_id": "sample_feed",
        "lifecycle_state": lifecycle,
        "supported_instruments": ["XAUUSD"],
        "historical_timeframes": list(TIMEFRAMES),
    }


def candle_rows(count=8, future=False):
    base = datetime.now(timezone.utc) - timedelta(days=40)
    if future:
        base = datetime.now(timezone.utc) + timedelta(days=2)
    return [
        {
            "timestamp": (base + timedelta(minutes=15 * i)).isoformat(),
            "open": 2000.0 + i,
            "high": 2002.0 + i,
            "low": 1999.0 + i,
            "close": 2001.0 + i,
            "volume": 100.0,
            "source": "sample_feed",
            "source_version": "test-v1",
            "session_convention": "UTC",
            "price_component": "MID",
        }
        for i in range(count)
    ]


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(
        api.provider_onboarding, "get_provider",
        lambda provider_id: provider_record(),
    )
    monkeypatch.setattr(
        api.provider_job_submission, "_approval_path",
        lambda provider_id: "/tmp/sample-feed-approval.json",
    )
    monkeypatch.setattr(
        api.provider_approval, "load_approval",
        lambda path: {"provider_id": "sample_feed"},
    )
    validate = Mock()
    monkeypatch.setattr(api.provider_approval, "validate_approval", validate)

    rows = {tf: candle_rows() for tf in TIMEFRAMES}
    retrieval = Mock(
        side_effect=lambda instrument, timeframe, **kwargs: rows[timeframe]
    )
    monkeypatch.setattr(
        api.historical_store, "get_historical_candles", retrieval
    )

    analysis = {
        "instrument": "XAUUSD",
        "probability": None,
        "trade_signal": None,
        "execution_enabled": False,
        "scenarios": [{"status": "WAIT", "confirmed": False}],
    }
    monkeypatch.setattr(
        api, "analyse_instrument_scenarios",
        lambda instrument, candles: analysis,
    )
    save = Mock()
    monkeypatch.setattr(api, "save_forecast", save)

    return {
        "rows": rows,
        "retrieval": retrieval,
        "validate": validate,
        "save": save,
    }


def test_stored_analysis_route_is_registered():
    from app.main import app

    assert "/v1/global/stored/analyse" in app.openapi()["paths"]


def test_stored_analysis_uses_latest_provider_candles_and_remains_wait(
    configured,
):
    result = api.analyse_stored_market(
        instrument="xauusd",
        source="sample_feed",
        candles_per_timeframe=120,
    )

    assert result["instrument"] == "XAUUSD"
    assert result["data_source"] == "PERSISTED_PROVIDER_HISTORICAL_CANDLES"
    assert result["provider_approval_validated"] is True
    assert result["data_provenance"]["licensing_approved_at_request"] is True
    assert result["data_provenance"]["market_data_independently_verified"] is False
    assert result["data_provenance"]["signal_eligible"] is False
    assert result["trade_signal"] is None
    assert result["probability"] is None
    assert result["alerts_emitted"] is False
    assert result["execution_enabled"] is False
    assert result["analysis"]["scenarios"][0]["status"] == "WAIT"
    assert configured["retrieval"].call_count == 4
    assert all(
        call.kwargs["latest"] is True
        and call.kwargs["source"] == "sample_feed"
        for call in configured["retrieval"].call_args_list
    )
    assert configured["validate"].call_count == 4
    configured["save"].assert_called_once()


def test_inactive_provider_cannot_display_stored_analysis(
    configured, monkeypatch,
):
    monkeypatch.setattr(
        api.provider_onboarding, "get_provider",
        lambda provider_id: provider_record("DISABLED"),
    )
    with pytest.raises(HTTPException) as error:
        api.analyse_stored_market("XAUUSD", "sample_feed", 100)

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "PROVIDER_NOT_ACTIVE"
    configured["retrieval"].assert_not_called()
    configured["save"].assert_not_called()


def test_expired_written_licence_blocks_analysis(configured, monkeypatch):
    from app.data.provider_contract import ProviderNotApproved

    def reject(*args, **kwargs):
        raise ProviderNotApproved("approval expired")

    monkeypatch.setattr(api.provider_approval, "validate_approval", reject)
    with pytest.raises(HTTPException) as error:
        api.analyse_stored_market("XAUUSD", "sample_feed", 100)

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "CURRENT_WRITTEN_DATA_LICENCE_REQUIRED"
    configured["retrieval"].assert_not_called()
    configured["save"].assert_not_called()


def test_incomplete_timeframe_history_is_rejected(configured):
    configured["rows"]["H4"] = candle_rows(7)
    with pytest.raises(HTTPException) as error:
        api.analyse_stored_market("XAUUSD", "sample_feed", 100)

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "INSUFFICIENT_STORED_HISTORY"
    assert error.value.detail["available_candles"]["H4"] == 7
    configured["save"].assert_not_called()


def test_future_stored_observations_are_rejected(configured):
    configured["rows"]["M15"] = candle_rows(8, future=True)
    with pytest.raises(HTTPException) as error:
        api.analyse_stored_market("XAUUSD", "sample_feed", 100)

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "FUTURE_OBSERVATIONS_PRESENT"
    configured["save"].assert_not_called()


def test_provider_scope_must_cover_all_timeframes(configured, monkeypatch):
    provider = provider_record()
    provider["historical_timeframes"] = ["D1", "H4", "H1"]
    monkeypatch.setattr(
        api.provider_onboarding, "get_provider",
        lambda provider_id: provider,
    )
    with pytest.raises(HTTPException) as error:
        api.analyse_stored_market("XAUUSD", "sample_feed", 100)

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "TIMEFRAME_OUTSIDE_PROVIDER_SCOPE"
    configured["retrieval"].assert_not_called()
    configured["save"].assert_not_called()
