import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, date

from fastapi.testclient import TestClient

from app.data import provider_onboarding
from app.data import provider_pipeline_store
from app.data import provider_registry
from app.data.provider_contract import ProviderMetadata

TEST_TOKEN = "oracle-fx-test-admin-token-0123456789"
PROVIDER_ID = "test_provider"
ORIGINAL_PROVIDER_FACTORIES = provider_registry.get_registered_provider_factories()


class FakeProvider:
    fetch_calls = 0

    def __init__(self):
        self.credentials_configured = True
        self.metadata = ProviderMetadata(
            provider_id=PROVIDER_ID,
            provider_version="test-v1",
            instrument_mapping={"XAUUSD": "GOLD"},
            supported_timeframes=frozenset({"M15"}),
            session_convention="UTC",
            price_component="MID",
            volume_semantics="TEST_ONLY",
            licensing_status="APPROVED",
        )

    def fetch_candles(self, request):
        type(self).fetch_calls += 1
        raise AssertionError("Submission must never fetch market data.")


@contextmanager
def client_context(tmp_path, monkeypatch):
    monkeypatch.setenv("ORACLE_FX_ADMIN_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", "test-only-placeholder")

    db_path = tmp_path / "oracle_fx_submission_test.db"
    monkeypatch.setattr(provider_onboarding, "DB_PATH", db_path)
    monkeypatch.setattr(provider_pipeline_store, "DB_PATH", db_path)

    from app import main

    monkeypatch.setattr(main, "initialise_database", lambda: None)
    monkeypatch.setattr(main, "initialise_historical_store", lambda: None)
    monkeypatch.setattr(main, "initialise_macro_store", lambda: None)

    with TestClient(main.app) as client:
        yield client


def configure_provider(tmp_path, monkeypatch, *, approved=True, activate=True):
    from app.data import provider_job_submission as submission

    provider_onboarding.register_provider(
        provider_id=PROVIDER_ID,
        display_name="Test Provider",
        adapter_key="test",
        credential_env_var="TEST_PROVIDER_API_KEY",
        supported_instruments=["XAUUSD"],
        historical_timeframes=["M15"],
    )

    if activate:
        provider_onboarding.set_provider_lifecycle(PROVIDER_ID, "ACTIVE")

    monkeypatch.setattr(
        provider_registry,
        "get_registered_provider_factories",
        lambda: {
            **ORIGINAL_PROVIDER_FACTORIES,
            PROVIDER_ID: FakeProvider,
        },
    )

    approval_dir = tmp_path / "approvals"
    approval_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(submission, "APPROVALS_DIR", approval_dir)

    if approved:
        today = date.today()
        record = {
            "provider_id": PROVIDER_ID,
            "status": "APPROVED",
            "approval_reference": "TEST-APPROVAL-001",
            "approved_at": today.isoformat(),
            "expires_at": (today + timedelta(days=365)).isoformat(),
            "commercial_use": True,
            "non_us_market_data_approval": True,
            "historical_storage": True,
            "derived_data_storage": True,
            "derived_data_display": True,
            "raw_data_redistribution": False,
            "permitted_instruments": ["XAUUSD"],
            "permitted_timeframes": ["M15"],
            "exchange_timezone": "UTC",
            "retention_days": 365,
            "attribution_required": False,
        }
        (approval_dir / f"{PROVIDER_ID}.json").write_text(
            json.dumps(record),
            encoding="utf-8",
        )

    FakeProvider.fetch_calls = 0


def auth():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def request_payload():
    now = datetime.now(timezone.utc)
    return {
        "instrument": "XAUUSD",
        "timeframe": "M15",
        "start": (now - timedelta(hours=2)).isoformat(),
        "end": (now - timedelta(hours=1)).isoformat(),
        "provider_ids": [PROVIDER_ID],
    }


def submit(client, payload=None, *, key="test-job-key-0001", headers=None):
    request_headers = {
        **auth(),
        "Idempotency-Key": key,
        **(headers or {}),
    }
    return client.post(
        "/v1/admin/ingestion-jobs",
        json=payload or request_payload(),
        headers=request_headers,
    )


def test_submission_requires_admin_authentication(tmp_path, monkeypatch):
    with client_context(tmp_path, monkeypatch) as client:
        response = client.post(
            "/v1/admin/ingestion-jobs",
            json=request_payload(),
            headers={"Idempotency-Key": "test-job-key-0001"},
        )
        assert response.status_code == 401


def test_submission_queues_without_fetching_and_is_idempotent(
    tmp_path, monkeypatch
):
    with client_context(tmp_path, monkeypatch) as client:
        configure_provider(tmp_path, monkeypatch)

        payload = request_payload()
        first = submit(client, payload)
        assert first.status_code == 202

        first_body = first.json()
        assert first_body["created"] is True
        assert first_body["submission_status"] == "QUEUED"
        assert first_body["provider_calls_performed"] is False
        assert first_body["execution_enabled"] is False
        assert first_body["provider_network_requests_enabled"] is False
        assert first_body["signal_eligible"] is False

        second = submit(client, payload)
        assert second.status_code == 200
        assert second.json()["created"] is False
        assert second.json()["job"]["job_id"] == first_body["job"]["job_id"]
        assert FakeProvider.fetch_calls == 0

        jobs = provider_pipeline_store.list_jobs(limit=20)
        assert len(jobs) == 1


def test_same_idempotency_key_with_different_request_conflicts(
    tmp_path, monkeypatch
):
    with client_context(tmp_path, monkeypatch) as client:
        configure_provider(tmp_path, monkeypatch)

        payload = request_payload()
        first = submit(client, payload)
        assert first.status_code == 202

        changed = dict(payload)
        changed["start"] = (
            datetime.fromisoformat(payload["start"]) - timedelta(minutes=15)
        ).isoformat()

        second = submit(client, changed)
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert FakeProvider.fetch_calls == 0


def test_submission_fails_without_written_licensing_approval(
    tmp_path, monkeypatch
):
    with client_context(tmp_path, monkeypatch) as client:
        configure_provider(tmp_path, monkeypatch, approved=False)

        response = submit(client)
        assert response.status_code == 409
        assert (
            response.json()["detail"]["code"]
            == "WRITTEN_LICENSE_APPROVAL_REQUIRED"
        )
        assert provider_pipeline_store.list_jobs(limit=20) == []
        assert FakeProvider.fetch_calls == 0


def test_submission_rejects_invalid_idempotency_key(tmp_path, monkeypatch):
    with client_context(tmp_path, monkeypatch) as client:
        configure_provider(tmp_path, monkeypatch)

        response = submit(client, key="bad")
        assert response.status_code == 422
        assert (
            response.json()["detail"]["code"]
            == "INVALID_IDEMPOTENCY_KEY"
        )
        assert provider_pipeline_store.list_jobs(limit=20) == []


def test_submission_rejects_unregistered_provider_scope(
    tmp_path, monkeypatch
):
    with client_context(tmp_path, monkeypatch) as client:
        configure_provider(tmp_path, monkeypatch)

        payload = request_payload()
        payload["timeframe"] = "H1"

        response = submit(client, payload)
        assert response.status_code == 409
        assert (
            response.json()["detail"]["code"]
            == "TIMEFRAME_SCOPE_UNSUPPORTED"
        )
        assert provider_pipeline_store.list_jobs(limit=20) == []
        assert FakeProvider.fetch_calls == 0


def test_submission_rejects_pending_provider_even_with_written_approval(
    tmp_path, monkeypatch
):
    with client_context(tmp_path, monkeypatch) as client:
        configure_provider(tmp_path, monkeypatch, activate=False)

        response = submit(client)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "PROVIDER_NOT_ACTIVE"
        assert provider_pipeline_store.list_jobs(limit=20) == []
        assert FakeProvider.fetch_calls == 0


def test_submission_rejects_disabled_provider(
    tmp_path, monkeypatch
):
    with client_context(tmp_path, monkeypatch) as client:
        configure_provider(tmp_path, monkeypatch)
        provider_onboarding.set_provider_lifecycle(PROVIDER_ID, "DISABLED")

        response = submit(client)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "PROVIDER_DISABLED"
        assert provider_pipeline_store.list_jobs(limit=20) == []
        assert FakeProvider.fetch_calls == 0


def test_identical_retry_succeeds_after_provider_is_disabled(
    tmp_path, monkeypatch
):
    with client_context(tmp_path, monkeypatch) as client:
        configure_provider(tmp_path, monkeypatch)
        payload = request_payload()

        first = submit(client, payload)
        assert first.status_code == 202

        provider_onboarding.set_provider_lifecycle(
            PROVIDER_ID, "DISABLED"
        )

        retry = submit(client, payload)
        assert retry.status_code == 200
        assert retry.json()["created"] is False
        assert retry.json()["submission_status"] == "ALREADY_EXISTS"
        assert retry.json()["job"]["job_id"] == first.json()["job"]["job_id"]
        assert retry.json()["provider_calls_performed"] is False
        assert retry.json()["execution_enabled"] is False
        assert FakeProvider.fetch_calls == 0


def test_identical_retry_succeeds_after_written_approval_is_withdrawn(
    tmp_path, monkeypatch
):
    with client_context(tmp_path, monkeypatch) as client:
        configure_provider(tmp_path, monkeypatch)
        payload = request_payload()

        first = submit(client, payload)
        assert first.status_code == 202

        approval_file = (
            tmp_path / "approvals" / f"{PROVIDER_ID}.json"
        )
        approval_file.unlink()

        retry = submit(client, payload)
        assert retry.status_code == 200
        assert retry.json()["created"] is False
        assert retry.json()["job"]["job_id"] == first.json()["job"]["job_id"]
        assert retry.json()["provider_calls_performed"] is False
        assert FakeProvider.fetch_calls == 0


def test_conflicting_retry_is_rejected_even_after_provider_is_disabled(
    tmp_path, monkeypatch
):
    with client_context(tmp_path, monkeypatch) as client:
        configure_provider(tmp_path, monkeypatch)
        payload = request_payload()

        first = submit(client, payload)
        assert first.status_code == 202

        provider_onboarding.set_provider_lifecycle(
            PROVIDER_ID, "DISABLED"
        )

        changed = dict(payload)
        changed["start"] = (
            datetime.fromisoformat(payload["start"])
            - timedelta(minutes=15)
        ).isoformat()

        retry = submit(client, changed)
        assert retry.status_code == 409
        assert retry.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert FakeProvider.fetch_calls == 0
