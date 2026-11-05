import pytest
from fastapi.testclient import TestClient

from app.data import provider_onboarding as store

TEST_TOKEN = "oracle-fx-test-admin-token-0123456789"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORACLE_FX_ADMIN_TOKEN", TEST_TOKEN)

    from app import main

    monkeypatch.setattr(
        store, "DB_PATH", tmp_path / "oracle_fx_admin_test.db"
    )
    monkeypatch.setattr(main, "initialise_database", lambda: None)
    monkeypatch.setattr(main, "initialise_historical_store", lambda: None)
    monkeypatch.setattr(main, "initialise_macro_store", lambda: None)

    with TestClient(main.app) as test_client:
        yield test_client


def auth():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def provider_payload():
    return {
        "provider_id": "sample_feed",
        "display_name": "Sample Feed",
        "adapter_key": "sample_adapter",
        "credential_env_var": "SAMPLE_FEED_API_KEY",
        "supported_instruments": ["XAUUSD", "EURUSD"],
        "historical_timeframes": ["D1", "H4", "H1", "M15"],
        "live_timeframes": ["H1", "M15"],
        "notes": "Test provider only.",
    }


def test_provider_routes_require_admin_authentication(client):
    response = client.get("/v1/admin/providers")
    assert response.status_code == 401


def test_admin_api_fails_closed_without_configured_token(
    client, monkeypatch
):
    monkeypatch.delenv("ORACLE_FX_ADMIN_TOKEN")
    response = client.get("/v1/admin/providers")
    assert response.status_code == 503


def test_short_admin_token_disables_admin_api(client, monkeypatch):
    monkeypatch.setenv("ORACLE_FX_ADMIN_TOKEN", "too-short")
    response = client.get("/v1/admin/providers")
    assert response.status_code == 503


def test_wrong_admin_token_is_rejected(client):
    response = client.get(
        "/v1/admin/providers",
        headers={"Authorization": "Bearer wrong-token-value"},
    )
    assert response.status_code == 401


def test_admin_can_register_and_list_pending_provider(client):
    response = client.post(
        "/v1/admin/providers",
        headers=auth(),
        json=provider_payload(),
    )
    assert response.status_code == 201
    provider = response.json()["provider"]
    assert provider["lifecycle_state"] == "PENDING_REVIEW"
    assert provider["credentials_configured"] is False
    assert provider["licensing_approved"] is False
    assert provider["signal_eligible"] is False

    listed = client.get("/v1/admin/providers", headers=auth())
    assert listed.status_code == 200
    assert len(listed.json()["providers"]) == 1


def test_duplicate_provider_returns_conflict(client):
    client.post(
        "/v1/admin/providers", headers=auth(), json=provider_payload()
    )
    response = client.post(
        "/v1/admin/providers", headers=auth(), json=provider_payload()
    )
    assert response.status_code == 409


def test_admin_can_activate_provider_but_not_approve_licensing(client):
    client.post(
        "/v1/admin/providers", headers=auth(), json=provider_payload()
    )

    rejected = client.patch(
        "/v1/admin/providers/sample_feed/lifecycle",
        headers=auth(),
        json={"lifecycle_state": "APPROVED"},
    )
    assert rejected.status_code == 422

    activated = client.patch(
        "/v1/admin/providers/sample_feed/lifecycle",
        headers=auth(),
        json={"lifecycle_state": "ACTIVE"},
    )
    assert activated.status_code == 200
    provider = activated.json()["provider"]
    assert provider["lifecycle_state"] == "ACTIVE"
    assert provider["licensing_approved"] is False
    assert provider["signal_eligible"] is False

    disabled = client.patch(
        "/v1/admin/providers/sample_feed/lifecycle",
        headers=auth(),
        json={"lifecycle_state": "DISABLED"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["provider"]["lifecycle_state"] == "DISABLED"


def test_unknown_provider_returns_not_found(client):
    response = client.patch(
        "/v1/admin/providers/missing/lifecycle",
        headers=auth(),
        json={"lifecycle_state": "DISABLED"},
    )
    assert response.status_code == 404


def test_connection_history_is_read_only_and_authenticated(client):
    client.post(
        "/v1/admin/providers", headers=auth(), json=provider_payload()
    )
    store.record_connection_test(
        "sample_feed", "SUCCEEDED", True, 120, "HTTP_200"
    )

    response = client.get(
        "/v1/admin/providers/sample_feed/connection-tests",
        headers=auth(),
    )
    assert response.status_code == 200
    assert len(response.json()["tests"]) == 1
    assert response.json()["tests"][0]["signal_eligible"] is False


def test_unknown_provider_history_returns_not_found(client):
    response = client.get(
        "/v1/admin/providers/missing/connection-tests",
        headers=auth(),
    )
    assert response.status_code == 404


def test_registration_rejects_unexpected_fields(client):
    payload = provider_payload()
    payload["api_key"] = "must-not-be-accepted"

    response = client.post(
        "/v1/admin/providers", headers=auth(), json=payload
    )
    assert response.status_code == 422


def test_connection_preflight_endpoint_requires_admin_auth(client):
    response = client.post(
        "/v1/admin/providers/sample_feed/connection-test"
    )
    assert response.status_code == 401


def test_connection_preflight_endpoint_records_not_run(client, monkeypatch):
    response = client.post(
        "/v1/admin/providers",
        headers=auth(),
        json=provider_payload(),
    )
    assert response.status_code == 201
    monkeypatch.delenv("SAMPLE_FEED_API_KEY", raising=False)

    result = client.post(
        "/v1/admin/providers/sample_feed/connection-test",
        headers=auth(),
    )
    assert result.status_code == 200
    payload = result.json()
    assert payload["connection_test"]["outcome"] == "NOT_RUN"
    assert payload["connection_test"]["diagnostic_code"] == "CREDENTIAL_MISSING"
    assert payload["connection_verified"] is False
    assert payload["network_request_performed"] is False
    assert payload["signal_eligible"] is False


def test_manual_ingestion_execution_endpoint_requires_admin(client):
    response = client.post(
        "/v1/admin/ingestion-jobs/example-job/execute"
    )
    assert response.status_code == 401


def test_manual_ingestion_execution_endpoint_returns_worker_result(
    client, monkeypatch
):
    from app.data import provider_ingestion_worker

    expected = {
        "job": {"job_id": "example-job", "status": "INGESTED"},
        "stored": {"inserted": 1, "duplicates": 0, "total_received": 1},
        "provider_failures": [],
        "worker_invoked": True,
        "historical_data_ingested": True,
        "trade_execution_enabled": False,
        "live_trading_enabled": False,
        "orders_submitted": False,
    }
    execute = lambda job_id: expected
    monkeypatch.setattr(
        provider_ingestion_worker,
        "execute_ingestion_job",
        execute,
    )

    response = client.post(
        "/v1/admin/ingestion-jobs/example-job/execute",
        headers=auth(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["job"]["status"] == "INGESTED"
    assert payload["historical_data_ingested"] is True
    assert payload["trade_execution_enabled"] is False
    assert payload["live_trading_enabled"] is False
    assert payload["orders_submitted"] is False


def test_manual_ingestion_execution_endpoint_maps_missing_job_to_404(
    client, monkeypatch
):
    from app.data import provider_ingestion_worker

    def missing_job(job_id):
        raise KeyError("not found")

    monkeypatch.setattr(
        provider_ingestion_worker,
        "execute_ingestion_job",
        missing_job,
    )

    response = client.post(
        "/v1/admin/ingestion-jobs/missing-job/execute",
        headers=auth(),
    )
    assert response.status_code == 404


def test_manual_ingestion_execution_endpoint_rejects_nonqueued_job(
    client, monkeypatch
):
    from app.data import provider_ingestion_worker
    from app.data.provider_pipeline_store import JobNotRunnable

    def not_queued(job_id):
        raise JobNotRunnable("already running")

    monkeypatch.setattr(
        provider_ingestion_worker,
        "execute_ingestion_job",
        not_queued,
    )

    response = client.post(
        "/v1/admin/ingestion-jobs/running-job/execute",
        headers=auth(),
    )
    assert response.status_code == 409


def test_manual_ingestion_execution_endpoint_hides_internal_errors(
    client, monkeypatch
):
    from app.data import provider_ingestion_worker

    def internal_error(job_id):
        raise RuntimeError("private api_key=DO_NOT_LEAK")

    monkeypatch.setattr(
        provider_ingestion_worker,
        "execute_ingestion_job",
        internal_error,
    )

    response = client.post(
        "/v1/admin/ingestion-jobs/example-job/execute",
        headers=auth(),
    )

    assert response.status_code == 500
    assert "DO_NOT_LEAK" not in response.text
    assert "api_key" not in response.text.lower()
