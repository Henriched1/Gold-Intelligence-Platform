"""Provider-neutral live quote validation.

Validates quote shape, timestamp bounds and configured spread limits.
Does not fetch data, authenticate providers, establish licensing,
verify sessions, or issue trading signals.
"""
from datetime import datetime, timezone
import math

from app.data.provider_contract import SUPPORTED_INSTRUMENTS


def _aware_utc(value, field):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            )
        except ValueError:
            raise ValueError(f"{field} must be valid ISO-8601") from None

    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field} must include a timezone offset")

    return value.astimezone(timezone.utc)


def _positive_finite(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive finite number")

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{field} must be a positive finite number"
        ) from None

    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field} must be a positive finite number")

    return number


def assess_live_quote(
    quote,
    *,
    now,
    max_age_seconds,
    max_spread_bps,
    max_future_skew_seconds=2,
    provider_licensed=False,
    provider_connection_verified=False,
):
    """Return fail-closed evidence for one bid/ask quote.

    Spread limits must come from explicit instrument-aware configuration.
    Provider licensing and connection flags must come from trusted
    upstream checks; this function does not establish those facts.
    """
    if not isinstance(quote, dict):
        raise ValueError("quote must be an object")

    symbol = quote.get("instrument")
    if (
        not isinstance(symbol, str)
        or symbol.strip().upper() not in SUPPORTED_INSTRUMENTS
    ):
        raise ValueError(
            "instrument is not in the canonical market universe"
        )
    symbol = symbol.strip().upper()

    source = quote.get("source")
    version = quote.get("source_version")

    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("source_version is required")

    bid = _positive_finite(quote.get("bid"), "bid")
    ask = _positive_finite(quote.get("ask"), "ask")

    if ask < bid:
        raise ValueError("ask must not be below bid")

    observed_at = _aware_utc(quote.get("timestamp"), "timestamp")
    checked_at = _aware_utc(now, "now")

    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(max_age_seconds)
        or max_age_seconds <= 0
    ):
        raise ValueError("max_age_seconds must be positive and finite")

    if (
        isinstance(max_future_skew_seconds, bool)
        or not isinstance(max_future_skew_seconds, (int, float))
        or not math.isfinite(max_future_skew_seconds)
        or max_future_skew_seconds < 0
    ):
        raise ValueError(
            "max_future_skew_seconds must be finite and non-negative"
        )

    spread_limit = _positive_finite(
        max_spread_bps, "max_spread_bps"
    )

    if (
        not isinstance(provider_licensed, bool)
        or not isinstance(provider_connection_verified, bool)
    ):
        raise ValueError("provider verification flags must be boolean")

    age_seconds = (checked_at - observed_at).total_seconds()
    future_timestamp = age_seconds < -max_future_skew_seconds
    age_for_freshness = max(0.0, age_seconds)
    data_fresh = (
        not future_timestamp
        and age_for_freshness <= max_age_seconds
    )

    midpoint = (bid + ask) / 2.0
    spread = ask - bid
    spread_bps = (spread / midpoint) * 10000.0

    spread_verified = True
    spread_valid = spread_bps <= spread_limit
    market_data_verified = (
        provider_licensed and provider_connection_verified
    )

    failures = []

    if future_timestamp:
        failures.append("QUOTE_TIMESTAMP_IN_FUTURE")
    elif not data_fresh:
        failures.append("QUOTE_STALE")

    if not provider_licensed:
        failures.append("PROVIDER_LICENSING_NOT_VERIFIED")

    if not provider_connection_verified:
        failures.append("PROVIDER_CONNECTION_NOT_VERIFIED")

    if not spread_valid:
        failures.append("SPREAD_EXCEEDS_CONFIGURED_LIMIT")

    return {
        "instrument": symbol,
        "source": source.strip(),
        "source_version": version.strip(),
        "quote_timestamp": observed_at.isoformat(),
        "checked_at": checked_at.isoformat(),
        "quote_age_seconds": round(age_for_freshness, 3),
        "bid": bid,
        "ask": ask,
        "mid": midpoint,
        "spread": spread,
        "spread_bps": round(spread_bps, 4),
        "max_spread_bps": spread_limit,
        "data_fresh": data_fresh,
        "market_data_verified": market_data_verified,
        "licensing_approved": provider_licensed,
        "provider_connection_verified": (
            provider_connection_verified
        ),
        "spread_verified": spread_verified,
        "spread_valid": spread_valid,
        "session_calendar_verified": False,
        "event_risk_clear": False,
        "signal_eligible": False,
        "status": (
            "QUOTE_EVIDENCE_VALIDATED" if not failures else "WAIT"
        ),
        "failures": failures,
        "warning": (
            "Quote validation does not establish session validity, "
            "macro-event coverage, entry structure, risk/reward or "
            "confirmed-signal eligibility."
        ),
    }
