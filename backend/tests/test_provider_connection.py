import pytest

from app.data import provider_onboarding as store
from app.data import provider_registry as registry
from app.data.provider_connection import run_provider_connection_preflight


@pytest.fixture
def prepared_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "provider_test.db")
    store.initialise_provider_onboarding_store()
    store.register_provider(
        provider_id="sample_feed",
        display_name="Sample Feed",
        adapter_key="sample_adapter",
        credential_env_var="SAMPLE_FEED_API_KEY",
        supported_instruments=["XAUUSD", "EURUSD"],
        historical_timeframes=["D1", "H4"],
        live_timeframes=["H4"],
    )
    registry.unregister_provider_factory("sample_feed")
    yield
    registry.unregister_provider_factory("sample_feed")


def make_adapter(
    provider_id="sample_feed",
    instruments=None,
    historical=None,
    live=None,
    licensing="APPROVED",
):
    class Metadata:
        pass

    metadata = Metadata()
    metadata.provider_id = provider_id
    metadata.instrument_mapping = instruments if instruments is not None else {
        "XAUUSD": "XAU/USD",
        "EURUSD": "EUR/USD",
    }
    metadata.supported_timeframes = historical if historical is not None else {"D1", "H4"}
    metadata.live_supported_timeframes = live if live is not None else {"H4"}
    metadata.licensing_status = licensing

    class Adapter:
        credentials_configured = True

    adapter = Adapter()
    adapter.metadata = metadata
    return adapter


def install_adapter(monkeypatch, adapter):
    monkeypatch.setattr(
        registry,
        "get_registered_provider_factories",
        lambda: {"sample_feed": lambda: adapter},
    )


def test_missing_credential_blocks(prepared_store):
    result = run_provider_connection_preflight("sample_feed", {})
    assert result["preflight_status"] == "BLOCKED"
    assert result["connection_test"]["outcome"] == "NOT_RUN"
    assert result["connection_test"]["diagnostic_code"] == "CREDENTIAL_MISSING"
    assert result["network_request_performed"] is False
    assert result["signal_eligible"] is False


def test_missing_adapter_blocks(prepared_store):
    result = run_provider_connection_preflight(
        "sample_feed", {"SAMPLE_FEED_API_KEY": "present"}
    )
    assert result["connection_test"]["diagnostic_code"] == "ADAPTER_UNAVAILABLE"


def test_adapter_initialization_error_is_sanitized(prepared_store, monkeypatch):
    def fail():
        raise RuntimeError("secret details must not escape")

    monkeypatch.setattr(
        registry, "get_registered_provider_factories",
        lambda: {"sample_feed": fail},
    )
    result = run_provider_connection_preflight(
        "sample_feed", {"SAMPLE_FEED_API_KEY": "present"}
    )
    assert result["connection_test"]["diagnostic_code"] == "ADAPTER_INITIALIZATION_FAILED"
    assert "secret" not in str(result)


def test_unapproved_license_blocks(prepared_store, monkeypatch):
    install_adapter(
        monkeypatch,
        make_adapter(licensing="NOT_APPROVED"),
    )
    result = run_provider_connection_preflight(
        "sample_feed", {"SAMPLE_FEED_API_KEY": "present"}
    )
    assert result["connection_test"]["diagnostic_code"] == "LICENSE_NOT_APPROVED"
    assert result["connection_verified"] is False


def test_valid_historical_and_live_scope_passes_local_checks(
    prepared_store, monkeypatch
):
    install_adapter(monkeypatch, make_adapter())
    result = run_provider_connection_preflight(
        "sample_feed", {"SAMPLE_FEED_API_KEY": "present"}
    )
    assert result["preflight_status"] == "READY_FOR_EXPLICIT_NETWORK_TEST"
    assert result["adapter_scope_validated"] is True
    assert result["connection_test"]["outcome"] == "NOT_RUN"
    assert result["connection_test"]["diagnostic_code"] == "NETWORK_TEST_NOT_ENABLED"
    assert result["connection_verified"] is False
    assert result["network_request_performed"] is False
    assert result["network_test_authorized"] is False
    assert result["licensing_approved"] is False
    assert result["signal_eligible"] is False


def test_adapter_provider_identity_must_match(prepared_store, monkeypatch):
    install_adapter(
        monkeypatch,
        make_adapter(provider_id="different_provider"),
    )
    result = run_provider_connection_preflight(
        "sample_feed", {"SAMPLE_FEED_API_KEY": "present"}
    )
    assert result["connection_test"]["diagnostic_code"] == "ADAPTER_PROVIDER_MISMATCH"


def test_every_declared_instrument_must_be_mapped(prepared_store, monkeypatch):
    install_adapter(
        monkeypatch,
        make_adapter(instruments={"XAUUSD": "XAU/USD"}),
    )
    result = run_provider_connection_preflight(
        "sample_feed", {"SAMPLE_FEED_API_KEY": "present"}
    )
    assert result["connection_test"]["diagnostic_code"] == "HISTORICAL_SCOPE_UNSUPPORTED"


def test_every_historical_timeframe_must_be_supported(prepared_store, monkeypatch):
    install_adapter(
        monkeypatch,
        make_adapter(historical={"D1"}),
    )
    result = run_provider_connection_preflight(
        "sample_feed", {"SAMPLE_FEED_API_KEY": "present"}
    )
    assert result["connection_test"]["diagnostic_code"] == "HISTORICAL_SCOPE_UNSUPPORTED"


def test_live_scope_is_checked_separately(prepared_store, monkeypatch):
    install_adapter(
        monkeypatch,
        make_adapter(live=set()),
    )
    result = run_provider_connection_preflight(
        "sample_feed", {"SAMPLE_FEED_API_KEY": "present"}
    )
    assert result["connection_test"]["diagnostic_code"] == "LIVE_SCOPE_UNSUPPORTED"


def test_disabled_provider_blocks(prepared_store, monkeypatch):
    store.set_provider_lifecycle("sample_feed", "DISABLED")
    monkeypatch.setattr(
        registry, "get_registered_provider_factories",
        lambda: pytest.fail("adapter must not be instantiated"),
    )
    result = run_provider_connection_preflight(
        "sample_feed", {"SAMPLE_FEED_API_KEY": "present"}
    )
    assert result["connection_test"]["diagnostic_code"] == "PROVIDER_DISABLED"


def test_unknown_provider_raises_key_error(prepared_store):
    with pytest.raises(KeyError):
        run_provider_connection_preflight("missing_provider", {})
