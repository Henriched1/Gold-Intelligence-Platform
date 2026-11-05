import sqlite3

import pytest

from app.data import provider_onboarding as store
from app.data import provider_onboarding as onboarding_db_module


@pytest.fixture
def onboarding_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "oracle_fx_test.db")
    store.initialise_provider_onboarding_store()
    return store


def add_provider(db):
    return db.register_provider(
        "sample_feed", "Sample Feed", "sample_adapter",
        "SAMPLE_FEED_API_KEY",
        ["XAUUSD", "EURUSD"],
        ["D1", "H4", "H1", "M15"],
        ["H1", "M15"],
    )


def test_new_provider_is_pending_and_fail_closed(onboarding_db):
    item = add_provider(onboarding_db)
    assert item["lifecycle_state"] == "PENDING_REVIEW"
    assert item["credentials_configured"] is False
    assert item["licensing_approved"] is False
    assert item["signal_eligible"] is False


def test_duplicate_provider_rejected(onboarding_db):
    add_provider(onboarding_db)
    with pytest.raises(sqlite3.IntegrityError):
        add_provider(onboarding_db)


@pytest.mark.parametrize("bad_id", ["", "Sample", "a", "bad id", "x" * 65])
def test_invalid_provider_id_rejected(onboarding_db, bad_id):
    with pytest.raises(ValueError):
        onboarding_db.register_provider(
            bad_id, "Feed", "adapter", "FEED_API_KEY",
            ["XAUUSD"], ["H1"],
        )


def test_invalid_instrument_rejected(onboarding_db):
    with pytest.raises(ValueError, match="unsupported"):
        onboarding_db.register_provider(
            "sample_feed", "Feed", "adapter", "FEED_API_KEY",
            ["NOT_REAL"], ["H1"],
        )


def test_live_scope_must_be_inside_historical_scope(onboarding_db):
    with pytest.raises(ValueError, match="within historical"):
        onboarding_db.register_provider(
            "sample_feed", "Feed", "adapter", "FEED_API_KEY",
            ["XAUUSD"], ["H1"], ["D1"],
        )


def test_secret_value_is_not_accepted_as_env_name(onboarding_db):
    with pytest.raises(ValueError, match="environment-variable"):
        onboarding_db.register_provider(
            "sample_feed", "Feed", "adapter", "actual-secret-value",
            ["XAUUSD"], ["H1"],
        )


def test_lifecycle_cannot_grant_approval(onboarding_db):
    add_provider(onboarding_db)
    with pytest.raises(ValueError):
        onboarding_db.set_provider_lifecycle("sample_feed", "APPROVED")


def test_disable_and_reenable_pending_review(onboarding_db):
    add_provider(onboarding_db)
    assert onboarding_db.set_provider_lifecycle(
        "sample_feed", "DISABLED"
    )["lifecycle_state"] == "DISABLED"
    assert onboarding_db.set_provider_lifecycle(
        "sample_feed", "PENDING_REVIEW"
    )["lifecycle_state"] == "PENDING_REVIEW"


def test_unknown_provider_lifecycle_raises(onboarding_db):
    with pytest.raises(KeyError):
        onboarding_db.set_provider_lifecycle("missing", "DISABLED")


def test_successful_test_does_not_enable_signals(onboarding_db):
    add_provider(onboarding_db)
    result = onboarding_db.record_connection_test(
        "sample_feed", "SUCCEEDED", True, 123, "HTTP_200"
    )
    assert result["connection_verified"] is True
    assert result["signal_eligible"] is False
    assert "api_key" not in result


@pytest.mark.parametrize(
    "outcome",
    ["FAILED", "AUTH_FAILED", "RATE_LIMITED", "TIMEOUT", "NOT_RUN"],
)
def test_unsuccessful_tests_are_not_verified(onboarding_db, outcome):
    add_provider(onboarding_db)
    result = onboarding_db.record_connection_test(
        "sample_feed", outcome, False, diagnostic_code="TEST_RESULT"
    )
    assert result["connection_verified"] is False
    assert result["signal_eligible"] is False


def test_raw_diagnostic_text_rejected(onboarding_db):
    add_provider(onboarding_db)
    with pytest.raises(ValueError):
        onboarding_db.record_connection_test(
            "sample_feed", "FAILED", False,
            diagnostic_code="API key leaked in response",
        )


def test_unknown_provider_test_rejected(onboarding_db):
    with pytest.raises(KeyError):
        onboarding_db.record_connection_test("missing", "FAILED", False)


def test_connection_history_is_append_only(onboarding_db):
    add_provider(onboarding_db)
    result = onboarding_db.record_connection_test(
        "sample_feed", "FAILED", False
    )
    with sqlite3.connect(onboarding_db.DB_PATH) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE provider_connection_tests SET outcome='SUCCEEDED' WHERE id=?",
                (result["id"],),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "DELETE FROM provider_connection_tests WHERE id=?",
                (result["id"],),
            )


def test_disabled_providers_can_be_filtered(onboarding_db):
    add_provider(onboarding_db)
    onboarding_db.set_provider_lifecycle("sample_feed", "DISABLED")
    assert onboarding_db.list_providers(include_disabled=False) == []


def test_connection_history_limit_and_order(onboarding_db):
    add_provider(onboarding_db)
    onboarding_db.record_connection_test(
        "sample_feed", "FAILED", False,
        tested_at="2026-01-01T00:00:00Z",
    )
    onboarding_db.record_connection_test(
        "sample_feed", "SUCCEEDED", True,
        tested_at="2026-01-02T00:00:00Z",
    )
    history = onboarding_db.list_connection_tests("sample_feed", limit=1)
    assert len(history) == 1
    assert history[0]["outcome"] == "SUCCEEDED"


def test_naive_test_timestamp_rejected(onboarding_db):
    add_provider(onboarding_db)
    with pytest.raises(ValueError, match="timezone"):
        onboarding_db.record_connection_test(
            "sample_feed", "FAILED", False,
            tested_at="2026-01-01T00:00:00",
        )


def test_invalid_test_latency_rejected(onboarding_db):
    add_provider(onboarding_db)
    with pytest.raises(ValueError):
        onboarding_db.record_connection_test(
            "sample_feed", "FAILED", False, latency_ms=-1
        )


def test_active_lifecycle_does_not_grant_licensing_or_signal_approval(
    onboarding_db
):
    add_provider(onboarding_db)

    item = onboarding_db.set_provider_lifecycle("sample_feed", "ACTIVE")

    assert item["lifecycle_state"] == "ACTIVE"
    assert item["licensing_approved"] is False
    assert item["signal_eligible"] is False


def test_legacy_lifecycle_migration_preserves_provider_and_test_history(
    tmp_path, monkeypatch
):
    legacy_path = tmp_path / "legacy_oracle_fx.db"
    monkeypatch.setattr(onboarding_db_module, "DB_PATH", legacy_path)

    with sqlite3.connect(legacy_path) as db:
        db.execute("""
            CREATE TABLE provider_onboarding (
                provider_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                adapter_key TEXT NOT NULL,
                lifecycle_state TEXT NOT NULL
                    CHECK(lifecycle_state IN ('PENDING_REVIEW','DISABLED')),
                credential_env_var TEXT NOT NULL,
                supported_instruments_json TEXT NOT NULL,
                historical_timeframes_json TEXT NOT NULL,
                live_timeframes_json TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        db.execute("""
            INSERT INTO provider_onboarding VALUES (
                'legacy_feed', 'Legacy Feed', 'legacy_adapter',
                'PENDING_REVIEW', 'LEGACY_API_KEY',
                '["XAUUSD"]', '["H1"]', '[]', '',
                '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00'
            )
        """)
        db.execute("""
            CREATE TABLE provider_connection_tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                tested_at TEXT NOT NULL,
                latency_ms INTEGER,
                credential_present INTEGER NOT NULL,
                diagnostic_code TEXT,
                FOREIGN KEY(provider_id)
                    REFERENCES provider_onboarding(provider_id)
            )
        """)
        db.execute("""
            INSERT INTO provider_connection_tests (
                provider_id, outcome, tested_at, latency_ms,
                credential_present, diagnostic_code
            ) VALUES (
                'legacy_feed', 'SUCCEEDED',
                '2026-01-02T00:00:00+00:00', 100, 1, 'HTTP_200'
            )
        """)

    onboarding_db_module.initialise_provider_onboarding_store()

    provider = onboarding_db_module.get_provider("legacy_feed")
    assert provider["lifecycle_state"] == "PENDING_REVIEW"

    with sqlite3.connect(legacy_path) as db:
        history = db.execute(
            "SELECT provider_id, outcome FROM provider_connection_tests"
        ).fetchall()
        foreign_key_errors = db.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

    assert history == [("legacy_feed", "SUCCEEDED")]
    assert foreign_key_errors == []

    activated = onboarding_db_module.set_provider_lifecycle(
        "legacy_feed", "ACTIVE"
    )
    assert activated["lifecycle_state"] == "ACTIVE"
    assert activated["licensing_approved"] is False
    assert activated["signal_eligible"] is False
