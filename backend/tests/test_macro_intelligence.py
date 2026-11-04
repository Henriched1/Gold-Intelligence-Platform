from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.data import macro_calendar_store as store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "macro-test.db")
    store.initialise_macro_store()
    with TestClient(app) as test_client:
        yield test_client


def payload(event_id="US-CPI-2027-01", scheduled_at="2027-01-12T13:30:00Z"):
    return {
        "event_id": event_id, "title": "US CPI", "event_type": "INFLATION",
        "scheduled_at": scheduled_at, "currency_codes": ["usd"], "impact": "HIGH",
        "source_name": "Test calendar", "source_url": "https://example.test/event",
        "actual": 3.1, "forecast": 3.0, "previous": 2.9, "unit": "%",
    }


def test_import_is_idempotent_and_unverified(client):
    body = {"events": [payload()]}
    first = client.post("/v1/macro/events/import", json=body)
    second = client.post("/v1/macro/events/import", json=body)
    assert first.status_code == 200 and first.json()["inserted"] == 1
    assert first.json()["source_status"] == "UNVERIFIED"
    assert second.json()["inserted"] == 0


def test_naive_event_timestamp_rejected(client):
    response = client.post("/v1/macro/events/import", json={"events": [payload(scheduled_at="2027-01-12T13:30:00")]})
    assert response.status_code == 422


def test_usd_event_exposure_for_gold_has_no_direction(client):
    client.post("/v1/macro/events/import", json={"events": [payload()]})
    response = client.post("/v1/macro/assess", json={
        "instrument": "XAUUSD", "as_of": "2027-01-12T13:00:00Z",
    })
    body = response.json()
    assert response.status_code == 200
    assert body["risk_state"] == "EVENT_WINDOW_ACTIVE_UNVERIFIED"
    assert body["event_risk_gate_passed"] is False
    assert body["directional_bias"] is None
    assert body["matching_events_in_window"][0]["actual_minus_forecast"] == pytest.approx(0.1)


def test_no_event_does_not_claim_calendar_clear(client):
    body = client.post("/v1/macro/assess", json={
        "instrument": "EURUSD", "as_of": "2027-01-12T10:00:00Z",
    }).json()
    assert body["risk_state"] == "NO_MATCHING_EVENT_FOUND_COVERAGE_UNVERIFIED"
    assert body["event_risk_gate_passed"] is False


def test_unknown_instrument_rejected(client):
    response = client.post("/v1/macro/assess", json={"instrument": "NOTREAL", "as_of": "2027-01-12T13:00:00Z"})
    assert response.status_code == 422


def test_currency_filter(client):
    client.post("/v1/macro/events/import", json={"events": [payload()]})
    assert client.get("/v1/macro/events?currency=USD").json()["count"] == 1
    assert client.get("/v1/macro/events?currency=EUR").json()["count"] == 0
