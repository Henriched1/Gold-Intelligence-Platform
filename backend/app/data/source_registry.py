"""Read-only multi-provider readiness and global capability matrix."""

from collections import Counter

from app.core.universe import INSTRUMENTS
from app.data.provider_contract import SUPPORTED_TIMEFRAMES
from app.data.provider_registry import (
    ensure_default_providers,
    get_registered_provider_factories,
)


def _provider_status(metadata, instrument, timeframe, credentials):
    mapping = getattr(metadata, "instrument_mapping", {})
    if instrument not in mapping:
        return None

    if getattr(metadata, "licensing_status", "NOT_APPROVED") != "APPROVED":
        historical = "HISTORICAL_LICENSING_NOT_APPROVED"
    elif timeframe not in getattr(metadata, "supported_timeframes", ()):
        historical = "HISTORICAL_SCOPE_NOT_APPROVED"
    elif not credentials:
        historical = "HISTORICAL_CREDENTIALS_MISSING"
    else:
        historical = "HISTORICAL_CONNECTION_NOT_TESTED"

    live_timeframes = getattr(metadata, "live_supported_timeframes", ())
    if timeframe not in live_timeframes:
        live = "LIVE_LICENSING_NOT_APPROVED"
    elif not credentials:
        live = "LIVE_CREDENTIALS_MISSING"
    else:
        live = "LIVE_CONNECTION_NOT_TESTED"

    return historical, live


def _best_status(statuses, index, no_provider):
    if not statuses:
        return no_provider

    priority = {
        "HISTORICAL_CONNECTION_NOT_TESTED": 5,
        "HISTORICAL_CREDENTIALS_MISSING": 4,
        "HISTORICAL_SCOPE_NOT_APPROVED": 3,
        "HISTORICAL_LICENSING_NOT_APPROVED": 2,
        "LIVE_CONNECTION_NOT_TESTED": 5,
        "LIVE_CREDENTIALS_MISSING": 4,
        "LIVE_LICENSING_NOT_APPROVED": 2,
    }
    return max((item[index] for item in statuses),
               key=lambda value: priority.get(value, 0))


def get_data_source_readiness(provider_factory=None, provider_factories=None):
    """Inspect registered providers without making network requests.

    provider_factory preserves the original single-provider test interface.
    provider_factories optionally supplies a mapping of provider IDs to
    factories for isolated tests or controlled application configuration.
    """
    try:
        if provider_factory is not None:
            factories = {"injected_provider": provider_factory}
        elif provider_factories is not None:
            factories = dict(provider_factories)
        else:
            ensure_default_providers()
            factories = get_registered_provider_factories()

        providers = []
        instances = []

        for registered_id, factory in sorted(factories.items()):
            provider = factory()
            metadata = provider.metadata
            provider_id = metadata.provider_id

            if not isinstance(provider_id, str) or not provider_id.strip():
                raise ValueError("provider metadata has invalid provider_id")

            credentials = bool(
                getattr(provider, "credentials_configured", False)
            )
            mapping = getattr(metadata, "instrument_mapping", {})
            historical_timeframes = getattr(
                metadata, "supported_timeframes", frozenset()
            )
            live_timeframes = getattr(
                metadata, "live_supported_timeframes", frozenset()
            )

            providers.append({
                "provider_id": provider_id,
                "registration_id": registered_id,
                "adapter_registered": True,
                "credentials_configured": credentials,
                "licensing_status": getattr(
                    metadata, "licensing_status", "NOT_APPROVED"
                ),
                "supported_instruments": sorted(mapping),
                "historical_timeframes": sorted(historical_timeframes),
                "live_timeframes": sorted(live_timeframes),
                "price_component": getattr(
                    metadata, "price_component", "UNKNOWN"
                ),
                "connection_verified": False,
            })
            instances.append((provider_id, provider, credentials))

        matrix = []
        historical_counts = Counter()
        live_counts = Counter()

        for instrument in INSTRUMENTS:
            for timeframe in sorted(SUPPORTED_TIMEFRAMES):
                candidates = []

                for provider_id, provider, credentials in instances:
                    result = _provider_status(
                        provider.metadata,
                        instrument.symbol,
                        timeframe,
                        credentials,
                    )
                    if result is not None:
                        candidates.append((provider_id, *result))

                historical_status = _best_status(
                    [(item[1], item[2]) for item in candidates],
                    0,
                    "NO_REGISTERED_PROVIDER",
                )
                live_status = _best_status(
                    [(item[1], item[2]) for item in candidates],
                    1,
                    "NO_REGISTERED_PROVIDER",
                )

                historical_counts[historical_status] += 1
                live_counts[live_status] += 1

                matrix.append({
                    "instrument": instrument.symbol,
                    "asset_class": instrument.asset_class,
                    "timeframe": timeframe,
                    "provider_id": (
                        candidates[0][0] if candidates else None
                    ),
                    "provider_ids": [item[0] for item in candidates],
                    "historical_data_status": historical_status,
                    "live_data_status": live_status,
                    "connection_verified": False,
                    "signal_eligible": False,
                })

        if not providers:
            overall_status = "NO_REGISTERED_PROVIDER"
        elif all(
            item["live_data_status"] == "NO_REGISTERED_PROVIDER"
            for item in matrix
        ):
            overall_status = "NO_REGISTERED_PROVIDER"
        elif any(
            item["live_data_status"] == "LIVE_CONNECTION_NOT_TESTED"
            for item in matrix
        ):
            overall_status = "LIVE_CONNECTION_NOT_TESTED"
        elif any(
            item["live_data_status"] == "LIVE_CREDENTIALS_MISSING"
            for item in matrix
        ):
            overall_status = "LIVE_CREDENTIALS_MISSING"
        else:
            overall_status = "LIVE_LICENSING_NOT_APPROVED"

        return {
            "architecture": "INDEPENDENT_PROVIDER_NEUTRAL",
            "status": overall_status,
            "market_universe_count": len(INSTRUMENTS),
            "timeframe_count": len(SUPPORTED_TIMEFRAMES),
            "provider_count": len(providers),
            "providers": providers,
            "historical_readiness_summary": dict(historical_counts),
            "live_readiness_summary": dict(live_counts),
            "instrument_timeframe_matrix": matrix,
            "network_requests_made": 0,
            "live_data_connected": False,
            "broker_connection": False,
            "trade_execution": False,
            "limitations": [
                "Readiness inspection makes no provider network requests.",
                "Provider registration does not establish licensing approval.",
                "Historical approval does not grant live-market-data rights.",
                "Credential presence does not prove authentication or connectivity.",
                "Only explicitly mapped and approved scopes are supported.",
                "No connection test has been performed.",
                "No instrument is signal-eligible from readiness metadata alone.",
            ],
        }

    except Exception as error:
        return {
            "architecture": "INDEPENDENT_PROVIDER_NEUTRAL",
            "status": "PROVIDER_CONFIGURATION_REVIEW_REQUIRED",
            "providers": [],
            "instrument_timeframe_matrix": [],
            "network_requests_made": 0,
            "live_data_connected": False,
            "broker_connection": False,
            "trade_execution": False,
            "configuration_error_type": type(error).__name__,
            "limitations": [
                "Provider configuration could not be inspected safely.",
                "No provider request was made.",
            ],
        }
