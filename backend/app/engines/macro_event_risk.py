"""Deterministic macro-event exposure/window assessment, not a price forecast."""
from datetime import datetime, timedelta

from app.core.universe import get_instrument


def instrument_exposures(symbol):
    item = get_instrument(symbol)
    if item is None or not item.enabled:
        raise ValueError("instrument is not in the enabled market universe")
    # Use the canonical instrument registry instead of guessing symbol suffixes.
    exposures = set()
    for code in (item.base, item.quote):
        if isinstance(code, str) and len(code) == 3 and code.isalpha():
            exposures.add(code.upper())
    if item.asset_class == "INDEX":
        exposures.add({"GER40": "EUR"}.get(item.symbol, "USD"))
    if item.asset_class == "ENERGY":
        exposures.add("USD")
    return sorted(exposures)


def assess_macro_event_risk(instrument, as_of, events, pre_minutes=60, post_minutes=30):
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone offset")
    symbol = instrument.strip().upper()
    exposures = instrument_exposures(symbol)
    matched = []
    for event in events:
        if not set(exposures).intersection(event["currency_codes"]):
            continue
        scheduled = event["scheduled_at"]
        if isinstance(scheduled, str):
            scheduled = datetime.fromisoformat(scheduled)
        if scheduled.tzinfo is None or scheduled.utcoffset() is None:
            continue
        start = scheduled - timedelta(minutes=pre_minutes)
        end = scheduled + timedelta(minutes=post_minutes)
        if start <= as_of <= end:
            actual = event.get("actual")
            forecast = event.get("forecast")
            matched.append({
                "event_id": event["event_id"],
                "title": event["title"],
                "event_type": event["event_type"],
                "scheduled_at": scheduled.isoformat(),
                "currency_codes": event["currency_codes"],
                "impact": event["impact"],
                "source_status": "UNVERIFIED",
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "actual_minus_forecast": actual - forecast if actual is not None and forecast is not None else None,
                "unit": event["unit"],
            })
    matched.sort(key=lambda event: event["scheduled_at"])
    active = any(event["impact"] in {"HIGH", "MEDIUM"} for event in matched)
    return {
        "instrument": symbol,
        "exposed_currencies": exposures,
        "as_of": as_of.isoformat(),
        "pre_event_minutes": pre_minutes,
        "post_event_minutes": post_minutes,
        "matching_events_in_window": matched,
        "risk_state": "EVENT_WINDOW_ACTIVE_UNVERIFIED" if active else "NO_MATCHING_EVENT_FOUND_COVERAGE_UNVERIFIED",
        "event_risk_gate_passed": False,
        "calendar_coverage_verified": False,
        "directional_bias": None,
        "probability": None,
        "trade_signal": None,
        "warning": "Calendar provenance and completeness are unverified. No matching imported event does not prove the market is event-risk-free.",
    }
