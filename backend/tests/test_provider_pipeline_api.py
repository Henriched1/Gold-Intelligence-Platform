from fastapi.testclient import TestClient

from app.data import provider_onboarding
from app.data import provider_pipeline_store
from app.data.provider_contract import ProviderMetadata, ProviderRequest

TEST_TOKEN = "oracle-fx-test-admin-token-0123456789"


class FakeProvider:
    metadata = ProviderMetadata(
        provider_id="provider_one",
        provider_version="test-v1",
        instrument_mapping={"XAUUSD": "GOLD"},
        supported_timeframes=frozenset({"D1", "H4", "H1", "M15"}),
        session_convention="UTC",
        price_component="MID",
        volume_semantics="TEST_ONLY",
        licensing_status="APPROVED",
    )


def make_request():
    return ProviderRequest(
        instrument="XAUUSD",
        timeframe="M15",
        start="2026-01-01T00:00:00Z",
        end="2026-01-01T01:00:00Z",
    )


def auth():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def make_finished_job():
    job, created = provider_pipeline_store.create_job(
        "api-test-job-key-0001",
        [FakeProvider()],
        make_request(),
    )
    assert created

    provider_pipeline_store.mark_job_running(job["job_id"])
    attempt_id = provider_pipeline_store.start_attempt(
        job["job_id"], "provider_one", 1
    )
    provider_pipeline_store.finish_attempt(
        job["job_id"],
        "provider_one",
        attempt_id,
        1,
        "UNAVAILABLE",
        "PROVIDER_UNAVAILABLE",
    )
    provider_pipeline_store.finish_job(
        job["job_id"],
        "UNAVAILABLE",
        {
            "status": "UNAVAILABLE",
            "attempt_count": 1,
            "failover_used": False,
            "diagnostic_code": "ALL_PROVIDERS_UNAVAILABLE",
        },
    )
    return job["job_id"]


def test_ingestion_job_routes_require_admin_authentication(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ORACLE_FX_ADMIN_TOKEN", TEST_TOKEN)
    db_path = tmp_path / "oracle_fx_pipeline_api.db"
    monkeypatch.setattr(provider_onboarding, "DB_PATH", db_path)
    monkeypatch.setattr(provider_pipeline_store, "DB_PATH", db_path)

    from app import main

    monkeypatch.setattr(main, "initialise_database", lambda: None)
    monkeypatch.setattr(main, "initialise_historical_store", lambda: None)
    monkeypatch.setattr(main, "initialise_macro_store", lambda: None)

    with TestClient(main.app) as client:
        assert client.get("/v1/admin/ingestion-jobs").status_code == 401
        assert client.get(
            "/v1/admin/ingestion-jobs/example"
        ).status_code == 401
        assert client.get(
            "/v1/admin/ingestion-jobs/example/events"
        ).status_code == 401


def test_admin_can_list_ingestion_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("ORACLE_FX_ADMIN_TOKEN", TEST_TOKEN)
    db_path = tmp_path / "oracle_fx_pipeline_api.db"
    monkeypatch.setattr(provider_onboarding, "DB_PATH", db_path)
    monkeypatch.setattr(provider_pipeline_store, "DB_PATH", db_path)

    from app import main

    monkeypatch.setattr(main, "initialise_database", lambda: None)
    monkeypatch.setattr(main, "initialise_historical_store", lambda: None)
    monkeypatch.setattr(main, "initialise_macro_store", lambda: None)

    with TestClient(main.app) as client:
        job_id = make_finished_job()
        response = client.get(
            "/v1/admin/ingestion-jobs",
            headers=auth(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["execution_enabled"] is False
    assert len(payload["jobs"]) == 1
    assert payload["jobs"][0]["job_id"] == job_id
    assert payload["jobs"][0]["status"] == "UNAVAILABLE"


def test_admin_can_read_job_without_sensitive_internal_fields(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ORACLE_FX_ADMIN_TOKEN", TEST_TOKEN)
    db_path = tmp_path / "oracle_fx_pipeline_api.db"
    monkeypatch.setattr(provider_onboarding, "DB_PATH", db_path)
    monkeypatch.setattr(provider_pipeline_store, "DB_PATH", db_path)

    from app import main

    monkeypatch.setattr(main, "initialise_database", lambda: None)
    monkeypatch.setattr(main, "initialise_historical_store", lambda: None)
    monkeypatch.setattr(main, "initialise_macro_store", lambda: None)

    with TestClient(main.app) as client:
        job_id = make_finished_job()
        response = client.get(
            f"/v1/admin/ingestion-jobs/{job_id}",
            headers=auth(),
        )

    assert response.status_code == 200
    job = response.json()["job"]
    assert job["job_id"] == job_id
    assert job["status"] == "UNAVAILABLE"
    assert "idempotency_key" not in job
    assert "request_fingerprint" not in job
    assert response.json()["execution_enabled"] is False


def test_admin_can_read_append_only_job_events(tmp_path, monkeypatch):
    monkeypatch.setenv("ORACLE_FX_ADMIN_TOKEN", TEST_TOKEN)
    db_path = tmp_path / "oracle_fx_pipeline_api.db"
    monkeypatch.setattr(provider_onboarding, "DB_PATH", db_path)
    monkeypatch.setattr(provider_pipeline_store, "DB_PATH", db_path)

    from app import main

    monkeypatch.setattr(main, "initialise_database", lambda: None)
    monkeypatch.setattr(main, "initialise_historical_store", lambda: None)
    monkeypatch.setattr(main, "initialise_macro_store", lambda: None)

    with TestClient(main.app) as client:
        job_id = make_finished_job()
        response = client.get(
            f"/v1/admin/ingestion-jobs/{job_id}/events",
            headers=auth(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["history_append_only"] is True
    assert payload["execution_enabled"] is False
    assert [
        event["event_type"] for event in payload["events"]
    ] == [
        "JOB_CREATED",
        "JOB_STARTED",
        "ATTEMPT_STARTED",
        "ATTEMPT_FINISHED",
        "JOB_FINISHED",
    ]
    assert payload["events"][3]["diagnostic_code"] == "PROVIDER_UNAVAILABLE"


def test_unknown_job_returns_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("ORACLE_FX_ADMIN_TOKEN", TEST_TOKEN)
    db_path = tmp_path / "oracle_fx_pipeline_api.db"
    monkeypatch.setattr(provider_onboarding, "DB_PATH", db_path)
    monkeypatch.setattr(provider_pipeline_store, "DB_PATH", db_path)

    from app import main

    monkeypatch.setattr(main, "initialise_database", lambda: None)
    monkeypatch.setattr(main, "initialise_historical_store", lambda: None)
    monkeypatch.setattr(main, "initialise_macro_store", lambda: None)

    with TestClient(main.app) as client:
        detail = client.get(
            "/v1/admin/ingestion-jobs/not-a-real-job",
            headers=auth(),
        )
        events = client.get(
            "/v1/admin/ingestion-jobs/not-a-real-job/events",
            headers=auth(),
        )

    assert detail.status_code == 404
    assert events.status_code == 404


def test_job_list_limit_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("ORACLE_FX_ADMIN_TOKEN", TEST_TOKEN)
    db_path = tmp_path / "oracle_fx_pipeline_api.db"
    monkeypatch.setattr(provider_onboarding, "DB_PATH", db_path)
    monkeypatch.setattr(provider_pipeline_store, "DB_PATH", db_path)

    from app import main

    monkeypatch.setattr(main, "initialise_database", lambda: None)
    monkeypatch.setattr(main, "initialise_historical_store", lambda: None)
    monkeypatch.setattr(main, "initialise_macro_store", lambda: None)

    with TestClient(main.app) as client:
        response = client.get(
            "/v1/admin/ingestion-jobs?limit=501",
            headers=auth(),
        )

    assert response.status_code == 422
