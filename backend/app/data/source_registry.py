"""Read-only provider registry. This module never performs network calls."""

from app.data.providers.twelve_data import TwelveDataProvider


def get_data_source_readiness():
    try:
        provider = TwelveDataProvider()
        metadata = provider.metadata
        credential = getattr(provider, "_api_key", None)
        credentials_configured = (
            isinstance(credential, str) and bool(credential.strip())
        )

        licensing = metadata.licensing_status
        if licensing != "APPROVED":
            state = "LICENSING_NOT_APPROVED"
        elif not credentials_configured:
            state = "APPROVED_CREDENTIALS_MISSING"
        else:
            state = "CONFIGURED_NOT_CONNECTION_TESTED"

        return {
            "architecture": "INDEPENDENT_PROVIDER_NEUTRAL",
            "status": state,
            "providers": [{
                "provider_id": metadata.provider_id,
                "adapter_registered": True,
                "credentials_configured": credentials_configured,
                "licensing_status": licensing,
                "supported_instruments": sorted(metadata.instrument_mapping),
                "supported_timeframes": sorted(metadata.supported_timeframes),
                "price_component": metadata.price_component,
                "connection_verified": False,
            }],
            "network_requests_made": 0,
            "live_data_connected": False,
            "broker_connection": False,
            "trade_execution": False,
            "limitations": [
                "Readiness inspection does not contact the provider.",
                "A configured API key does not prove connectivity or data rights.",
                "Twelve Data remains limited to its explicitly approved scope.",
                "Other providers can be added through the provider-neutral adapter contract.",
            ],
        }
    except Exception:
        return {
            "architecture": "INDEPENDENT_PROVIDER_NEUTRAL",
            "status": "PROVIDER_CONFIGURATION_REVIEW_REQUIRED",
            "providers": [],
            "network_requests_made": 0,
            "live_data_connected": False,
            "broker_connection": False,
            "trade_execution": False,
            "limitations": [
                "Provider configuration could not be inspected safely.",
                "No provider request was made.",
            ],
        }
