"""Provider-neutral contracts for historical market-data adapters."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


from app.core.universe import INSTRUMENTS

SUPPORTED_INSTRUMENTS = frozenset(
    item.symbol for item in INSTRUMENTS if item.enabled
)
SUPPORTED_TIMEFRAMES = frozenset({"D1", "H4", "H1", "M15"})
PRICE_COMPONENTS = frozenset({"BID", "ASK", "MID", "UNKNOWN"})
LICENSING_STATES = frozenset({
    "APPROVED",
    "NOT_APPROVED",
    "PENDING_REVIEW",
})


class ProviderError(Exception):
    """Base class for expected provider failures."""


class ProviderUnavailable(ProviderError):
    """Provider is temporarily unavailable."""


class ProviderRateLimited(ProviderError):
    """Provider has applied a request rate limit."""


class ProviderResponseError(ProviderError):
    """Provider returned an invalid or unsupported response."""


class ProviderNotApproved(ProviderError):
    """Provider has not passed the licensing approval gate."""


@dataclass(frozen=True)
class ProviderMetadata:
    provider_id: str
    provider_version: str
    instrument_mapping: dict[str, str]
    supported_timeframes: frozenset[str]
    session_convention: str
    price_component: str
    volume_semantics: str
    licensing_status: str = "NOT_APPROVED"
    live_supported_timeframes: frozenset[str] = frozenset()

    def validate(self, instrument: str, timeframe: str) -> None:
        required = {
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "session_convention": self.session_convention,
            "volume_semantics": self.volume_semantics,
        }

        if any(
            not isinstance(value, str) or not value.strip()
            for value in required.values()
        ):
            raise ValueError("provider metadata fields must be non-empty")

        if self.price_component not in PRICE_COMPONENTS:
            raise ValueError("invalid provider price component")

        if self.licensing_status not in LICENSING_STATES:
            raise ValueError("invalid provider licensing status")

        if self.licensing_status != "APPROVED":
            raise ProviderNotApproved(
                "provider licensing approval is required"
            )

        if instrument not in SUPPORTED_INSTRUMENTS:
            raise ValueError("unsupported canonical instrument")

        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise ValueError("unsupported canonical timeframe")

        if timeframe not in self.supported_timeframes:
            raise ValueError("provider does not support requested timeframe")

        mapped = self.instrument_mapping.get(instrument)

        if not isinstance(mapped, str) or not mapped.strip():
            raise ValueError("provider instrument mapping is missing")

    def validate_live(self, instrument: str, timeframe: str) -> None:
        """Validate historical scope and separate live-feed permission."""
        self.validate(instrument, timeframe)
        if timeframe not in self.live_supported_timeframes:
            raise ProviderNotApproved(
                "provider live-data permission is not approved"
            )


@dataclass(frozen=True)
class ProviderRequest:
    instrument: str
    timeframe: str
    start: str
    end: str


@runtime_checkable
class HistoricalDataProvider(Protocol):
    """Adapters return observations; they do not write to the database."""

    metadata: ProviderMetadata

    def fetch_candles(self, request: ProviderRequest) -> list[dict]:
        """Fetch a bounded request and return provider observations."""
        ...
