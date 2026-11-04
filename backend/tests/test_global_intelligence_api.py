from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.api.global_intelligence import (
    GlobalAnalysisRequest,
    GlobalCandle,
    analyse_global_market,
    market_universe,
)
from app.main import app


def make_candles():
    return [
        GlobalCandle(
            timestamp=f"2026-01-01T00:{i:02d}:00+00:00",
            open=100 + i,
            high=102 + i,
            low=99 + i,
            close=101 + i,
            volume=10,
        )
        for i in range(8)
    ]


def make_request(instrument="EURUSD"):
    data = make_candles()
    return GlobalAnalysisRequest(
        instrument=instrument,
        d1=data,
        h4=data,
        h1=data,
        m15=data,
    )


def test_routes_are_registered():
    paths = set(app.openapi()["paths"])
    assert "/v1/universe" in paths
    assert "/v1/global/analyse" in paths


def test_universe_does_not_claim_live_feed_coverage():
    result = market_universe("FX")

    assert result["count"] > 0
    assert all(x["asset_class"] == "FX" for x in result["instruments"])
    assert result["live_data_available"] is False
    assert result["provider_coverage_verified"] is False
    assert result["execution_enabled"] is False


def test_unknown_asset_class_is_rejected():
    with pytest.raises(HTTPException) as error:
        market_universe("ALIENS")
    assert error.value.status_code == 422


def test_global_analysis_persists_research_only_record():
    analysis = {
        "instrument": "EURUSD",
        "probability": None,
        "trade_signal": None,
        "execution_enabled": False,
    }

    with patch(
        "app.api.global_intelligence.analyse_instrument_scenarios",
        return_value=analysis,
    ), patch(
        "app.api.global_intelligence.save_forecast",
    ) as save:
        result = analyse_global_market(make_request())

    save.assert_called_once()
    assert result["instrument"] == "EURUSD"
    assert result["horizon"] == "GLOBAL_MULTI_TIMEFRAME"
    assert result["status"] == "RESEARCH_ONLY_WAIT"
    assert result["probability"] is None
    assert result["trade_signal"] is None
    assert result["alerts_emitted"] is False
    assert result["execution_enabled"] is False


def test_unknown_instrument_returns_422_without_saving():
    with patch(
        "app.api.global_intelligence.analyse_instrument_scenarios",
        side_effect=ValueError("unknown instrument"),
    ), patch(
        "app.api.global_intelligence.save_forecast",
    ) as save:
        with pytest.raises(HTTPException) as error:
            analyse_global_market(make_request("FAKEUSD"))

    assert error.value.status_code == 422
    save.assert_not_called()
