from datetime import datetime, timedelta, timezone

import pytest

from app.data.live_quote_pipeline import assess_live_quote


NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def quote(**overrides):
    result = {
        "instrument": "XAUUSD",
        "bid": 2700.00,
        "ask": 2700.02,
        "timestamp": (NOW - timedelta(seconds=3)).isoformat(),
        "source": "sample_provider",
        "source_version": "test-v1",
    }
    result.update(overrides)
    return result


def assess(item=None, **overrides):
    options = {
        "now": NOW,
        "max_age_seconds": 10,
        "max_spread_bps": 3,
        "provider_licensed": True,
        "provider_connection_verified": True,
    }
    options.update(overrides)
    return assess_live_quote(item or quote(), **options)


def test_valid_quote_passes_quote_checks_but_not_signal_gate():
    result = assess()

    assert result["status"] == "QUOTE_EVIDENCE_VALIDATED"
    assert result["data_fresh"] is True
    assert result["market_data_verified"] is True
    assert result["licensing_approved"] is True
    assert result["spread_verified"] is True
    assert result["spread_valid"] is True
    assert result["session_calendar_verified"] is False
    assert result["signal_eligible"] is False


def test_stale_quote_returns_wait():
    item = quote(timestamp=(NOW - timedelta(seconds=30)).isoformat())

    result = assess(item, max_age_seconds=10)

    assert result["status"] == "WAIT"
    assert result["data_fresh"] is False
    assert "QUOTE_STALE" in result["failures"]


def test_future_quote_returns_wait():
    item = quote(timestamp=(NOW + timedelta(seconds=10)).isoformat())

    result = assess(item)

    assert result["status"] == "WAIT"
    assert "QUOTE_TIMESTAMP_IN_FUTURE" in result["failures"]


def test_excessive_spread_returns_wait():
    item = quote(bid=2700.0, ask=2702.0)

    result = assess(item, max_spread_bps=3)

    assert result["status"] == "WAIT"
    assert result["spread_verified"] is True
    assert result["spread_valid"] is False
    assert "SPREAD_EXCEEDS_CONFIGURED_LIMIT" in result["failures"]


def test_missing_provider_licensing_or_connection_fails_closed():
    result = assess(
        provider_licensed=False,
        provider_connection_verified=False,
    )

    assert result["status"] == "WAIT"
    assert result["market_data_verified"] is False
    assert "PROVIDER_LICENSING_NOT_VERIFIED" in result["failures"]
    assert "PROVIDER_CONNECTION_NOT_VERIFIED" in result["failures"]


def test_ask_below_bid_is_rejected():
    with pytest.raises(ValueError, match="ask must not be below bid"):
        assess(quote(bid=2701, ask=2700))


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError, match="timezone offset"):
        assess(quote(timestamp="2026-11-05T12:00:00"))


def test_unknown_instrument_is_rejected():
    with pytest.raises(ValueError, match="canonical market universe"):
        assess(quote(instrument="UNKNOWN"))


def test_missing_spread_limit_is_rejected():
    with pytest.raises(ValueError, match="max_spread_bps"):
        assess(quote(), max_spread_bps=0)


def test_boolean_price_is_rejected():
    with pytest.raises(ValueError, match="bid"):
        assess(quote(bid=True))
