"""Fail-closed Twelve Data historical candle adapter."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.data.provider_approval import (
    PROVIDER_ID,
    load_approval,
    validate_approval,
)
from app.data.provider_contract import (
    ProviderMetadata,
    ProviderNotApproved,
    ProviderRateLimited,
    ProviderRequest,
    ProviderResponseError,
    ProviderUnavailable,
)


API_URL = "https://api.twelvedata.com/time_series"
API_VERSION = "time-series-v1"
MAX_OUTPUTSIZE = 5000

TIMEFRAME_INTERVALS = {
    "M15": "15min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1day",
}


def parse_request_datetime(value):
    try:
        parsed = datetime.fromisoformat(
            value.strip().replace("Z", "+00:00")
        )
    except (AttributeError, ValueError) as error:
        raise ValueError("request dates must be ISO-8601") from None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("request dates must include a timezone")

    return parsed.astimezone(timezone.utc)


class TwelveDataProvider:
    """Adapter that requires documented licensing approval before fetching."""

    def __init__(self, approval_path=None, api_key=None, timeout=20):
        project_root = Path(__file__).resolve().parents[4]

        self.approval_path = Path(
            approval_path
            or project_root / "config" / "provider_approvals.json"
        )

        self._api_key = (
            api_key
            if api_key is not None
            else os.getenv("TWELVE_DATA_API_KEY")
        )

        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or timeout <= 0
        ):
            raise ValueError("timeout must be positive")

        self.timeout = float(timeout)
        self.metadata = self._build_metadata()

    def _build_metadata(self):
        record = load_approval(self.approval_path)
        approved_timeframes = set()

        if record is not None:
            for timeframe in TIMEFRAME_INTERVALS:
                try:
                    validate_approval(
                        record,
                        "XAUUSD",
                        timeframe,
                    )
                except ProviderNotApproved:
                    continue

                approved_timeframes.add(timeframe)

        is_approved = bool(approved_timeframes)

        return ProviderMetadata(
            provider_id=PROVIDER_ID,
            provider_version=API_VERSION,
            instrument_mapping={"XAUUSD": "XAU/USD"},
            supported_timeframes=frozenset(approved_timeframes),
            session_convention=(
                "Intraday timestamps requested in UTC; daily timestamps "
                "interpreted using the approved exchange timezone"
            ),
            price_component="UNKNOWN",
            volume_semantics=(
                "Provider-reported volume only; missing volume is unavailable"
            ),
            licensing_status=(
                "APPROVED" if is_approved else "NOT_APPROVED"
            ),
        )

    def _approved_record(self, request):
        record = load_approval(self.approval_path)

        return validate_approval(
            record,
            request.instrument,
            request.timeframe,
        )

    @staticmethod
    def _normalise_provider_timestamp(value, timezone_name):
        if not isinstance(value, str):
            raise ProviderResponseError(
                "provider timestamp is invalid"
            )

        try:
            parsed = datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            )
        except ValueError as error:
            raise ProviderResponseError(
                "provider timestamp is invalid"
            ) from None

        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(
                tzinfo=ZoneInfo(timezone_name)
            )

        return parsed.astimezone(timezone.utc).isoformat()

    def _build_params(self, request, record):
        timezone_name = (
            record["exchange_timezone"]
            if request.timeframe == "D1"
            else "UTC"
        )
        request_zone = ZoneInfo(timezone_name)
        start = parse_request_datetime(request.start).astimezone(
            request_zone
        )
        end = parse_request_datetime(request.end).astimezone(
            request_zone
        )

        if start > end:
            raise ValueError("request start must not be after end")

        return {
            "symbol": self.metadata.instrument_mapping[request.instrument],
            "interval": TIMEFRAME_INTERVALS[request.timeframe],
            "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
            "outputsize": str(MAX_OUTPUTSIZE),
            "order": "ASC",
            "format": "JSON",
            "timezone": timezone_name,
        }

    def fetch_candles(self, request: ProviderRequest) -> list[dict]:
        """Fetch one bounded candle batch after approval validation."""

        if not isinstance(request, ProviderRequest):
            raise ValueError("request must be a ProviderRequest")

        if (
            request.instrument != "XAUUSD"
            or request.timeframe not in TIMEFRAME_INTERVALS
        ):
            raise ValueError("unsupported instrument or timeframe")

        start_utc = parse_request_datetime(request.start)
        end_utc = parse_request_datetime(request.end)

        if start_utc > end_utc:
            raise ValueError("request start must not be after end")

        # Licensing approval is checked before any outbound request.
        record = self._approved_record(request)

        if not self._api_key or not self._api_key.strip():
            raise ProviderUnavailable(
                "Twelve Data API credentials are not configured"
            )

        params = self._build_params(request, record)
        url = f"{API_URL}?{urlencode(params)}"

        http_request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"apikey {self._api_key}",
            },
        )

        try:
            with urlopen(
                http_request,
                timeout=self.timeout,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))

        except HTTPError as error:
            if error.code == 429:
                raise ProviderRateLimited(
                    "Twelve Data rate limit reached"
                ) from None

            raise ProviderUnavailable(
                "Twelve Data HTTP request failed"
            ) from None

        except (URLError, TimeoutError, OSError):
            raise ProviderUnavailable(
                "Twelve Data is temporarily unavailable"
            ) from None

        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderResponseError(
                "Twelve Data returned invalid JSON"
            ) from None

        if not isinstance(payload, dict):
            raise ProviderResponseError(
                "Twelve Data response must be an object"
            )

        if (
            payload.get("status") == "error"
            or ("code" in payload and "values" not in payload)
        ):
            code = str(payload.get("code", ""))
            if code in {"429", "4290"}:
                raise ProviderRateLimited(
                    "Twelve Data rate limit reached"
                )
            raise ProviderResponseError(
                "Twelve Data reported a provider error"
            )

        values = payload.get("values")

        if not isinstance(values, list) or not values:
            raise ProviderResponseError(
                "Twelve Data returned no candle values"
            )

        if len(values) > MAX_OUTPUTSIZE:
            raise ProviderResponseError(
                "Twelve Data response exceeds row limit"
            )

        timestamp_timezone = (
            record["exchange_timezone"]
            if request.timeframe == "D1"
            else "UTC"
        )

        observations = []
        seen_timestamps = set()
        previous_timestamp = None

        for item in values:
            if not isinstance(item, dict):
                raise ProviderResponseError(
                    "Twelve Data candle must be an object"
                )

            timestamp = self._normalise_provider_timestamp(
                item.get("datetime"),
                timestamp_timezone,
            )
            timestamp_utc = datetime.fromisoformat(timestamp)

            if not start_utc <= timestamp_utc <= end_utc:
                raise ProviderResponseError(
                    "provider returned a candle outside the requested range"
                )

            if timestamp in seen_timestamps:
                raise ProviderResponseError(
                    "provider returned duplicate candle timestamps"
                )

            if (
                previous_timestamp is not None
                and timestamp_utc < previous_timestamp
            ):
                raise ProviderResponseError(
                    "provider candles are not chronological"
                )

            seen_timestamps.add(timestamp)
            previous_timestamp = timestamp_utc

            observation = {
                "timestamp": timestamp,
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": item.get("close"),
            }

            if item.get("volume") is None:
                observation["volume"] = None
                observation["volume_available"] = False
            else:
                observation["volume"] = item["volume"]
                observation["volume_available"] = True

            observations.append(observation)

        return observations
