"""Central registry for independent market-data provider adapters."""

from threading import RLock

_lock = RLock()
_factories = {}


def register_provider_factory(provider_id, factory, *, replace=False):
    """Register an adapter factory without contacting its provider."""
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ValueError("provider_id must be non-empty")
    if not callable(factory):
        raise TypeError("provider factory must be callable")

    with _lock:
        if provider_id in _factories and not replace:
            raise ValueError("provider is already registered")
        _factories[provider_id] = factory


def unregister_provider_factory(provider_id):
    """Remove a provider registration."""
    with _lock:
        return _factories.pop(provider_id, None) is not None


def get_registered_provider_factories():
    """Return a snapshot of registered provider factories."""
    with _lock:
        return dict(_factories)


def _twelve_data_factory():
    from app.data.providers.twelve_data import TwelveDataProvider
    return TwelveDataProvider()


def ensure_default_providers():
    """Register the existing adapter once, without network activity."""
    with _lock:
        if "twelve_data" not in _factories:
            _factories["twelve_data"] = _twelve_data_factory
