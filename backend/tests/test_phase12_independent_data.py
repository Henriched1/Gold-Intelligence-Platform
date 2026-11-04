from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def series(symbol, factor=1.0):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {
        "symbol": symbol,
        "candles": [
            {
                "timestamp": (start + timedelta(hours=i)).isoformat(),
                "close": 100 + factor * (i + i * i / 10),
            }
            for i in range(21)
        ],
    }


def test_global_universe_is_accepted_for_descriptive_cross_asset_analysis(client):
    response = client.post("/v1/cross-asset/analyse", json={
        "timeframe": "H1",
        "instruments": [series("EURUSD"), series("BTCUSD")],
    })
    assert response.status_code == 200
    body = response.json()
    assert body["source_status"] == "USER_SUPPLIED_UNVERIFIED"
    assert body["live_data_connected"] is False
    assert body["trade_signal"] is None
    assert body["alerts_emitted"] is False
    assert body["timeframe_validation"] == (
        "CALLER_DECLARED_NOT_INDEPENDENTLY_VALIDATED"
    )
    assert len(body["pairwise_correlations"]) == 1


def test_correlation_uses_aligned_timestamps(client):
    response = client.post("/v1/cross-asset/analyse", json={
        "timeframe": "H1",
        "instruments": [series("EURUSD"), series("GBPUSD")],
    })
    assert response.status_code == 200
    correlation = response.json()["pairwise_correlations"][0]
    assert correlation["aligned_return_observations"] == 20
    assert correlation["pearson_correlation"] == pytest.approx(1.0)


def test_unknown_instrument_is_rejected(client):
    response = client.post("/v1/cross-asset/analyse", json={
        "timeframe": "H1",
        "instruments": [series("NOTREAL"), series("EURUSD")],
    })
    assert response.status_code == 422


def test_naive_timestamp_is_rejected(client):
    item = series("EURUSD")
    item["candles"][0]["timestamp"] = "2026-01-01T00:00:00"
    response = client.post("/v1/cross-asset/analyse", json={
        "timeframe": "H1",
        "instruments": [item, series("GBPUSD")],
    })
    assert response.status_code == 422


def test_provider_readiness_does_not_make_network_requests(client):
    response = client.get("/v1/data-sources/readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["network_requests_made"] == 0
    assert body["live_data_connected"] is False
    assert body["trade_execution"] is False
    assert all("api_key" not in str(item).lower() for item in body["providers"])


def test_correlation_excludes_returns_with_different_interval_endpoints(client):
    first = series("EURUSD")
    second = series("GBPUSD")
    # Preserve the 21-observation minimum while creating a one-hour gap.
    for index in range(9, len(second["candles"])):
        timestamp = datetime.fromisoformat(
            second["candles"][index]["timestamp"]
        )
        second["candles"][index]["timestamp"] = (
            timestamp + timedelta(hours=1)
        ).isoformat()

    response = client.post("/v1/cross-asset/analyse", json={
        "timeframe": "H1",
        "instruments": [first, second],
    })

    assert response.status_code == 200
    correlation = response.json()["pairwise_correlations"][0]
    assert correlation["aligned_return_observations"] == 18
    assert correlation["pearson_correlation"] is None
    assert correlation["status"] == (
        "INSUFFICIENT_OR_CONSTANT_ALIGNED_RETURNS"
    )
