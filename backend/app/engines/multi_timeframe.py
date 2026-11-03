from datetime import datetime, timezone
from math import isfinite
from statistics import median

TIMEFRAMES = ("D1", "H4", "H1", "M15")


def validate_candles(candles):
    if len(candles) < 8:
        raise ValueError("At least 8 candles are required")

    previous_time = None

    for index, candle in enumerate(candles):
        for field in ("timestamp", "open", "high", "low", "close"):
            if field not in candle:
                raise ValueError(f"Candle {index} is missing {field}")

        try:
            stamp = datetime.fromisoformat(
                str(candle["timestamp"]).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            raise ValueError(f"Invalid timestamp at candle {index}")

        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError(
                f"Timestamp at candle {index} must include a timezone"
            )

        stamp = stamp.astimezone(timezone.utc)

        if previous_time is not None and stamp <= previous_time:
            raise ValueError("Candles must be chronological and unique")

        previous_time = stamp

        try:
            values = [
                float(candle[field])
                for field in ("open", "high", "low", "close")
            ]
        except (TypeError, ValueError, OverflowError):
            raise ValueError(
                f"Candle prices must be numeric at candle {index}"
            )

        if "volume" in candle:
            try:
                volume = float(candle["volume"])
            except (TypeError, ValueError, OverflowError):
                raise ValueError(
                    f"Volume must be numeric at candle {index}"
                )
            if not isfinite(volume) or volume < 0:
                raise ValueError(
                    f"Volume must be finite and non-negative at candle {index}"
                )

        if not all(isfinite(value) for value in values):
            raise ValueError("Candle prices must be finite numbers")

        opening, high, low, close = values

        if high < max(opening, close) or low > min(opening, close):
            raise ValueError(f"Invalid OHLC values at candle {index}")

        if high < low:
            raise ValueError(f"High is below low at candle {index}")


def find_confirmed_swings(candles, left=2, right=2):
    highs = []
    lows = []

    # A pivot is only known after its right-side candles exist.
    for i in range(left, len(candles) - right):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])

        neighbours = candles[i-left:i] + candles[i+1:i+right+1]

        if all(high > float(c["high"]) for c in neighbours):
            highs.append({"index": i, "price": high})

        if all(low < float(c["low"]) for c in neighbours):
            lows.append({"index": i, "price": low})

    return highs, lows


def analyse_timeframe(timeframe, candles):
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    validate_candles(candles)

    highs, lows = find_confirmed_swings(candles)
    current = candles[-1]

    events = []
    directional_evidence = []

    last_high = highs[-1] if highs else None
    last_low = lows[-1] if lows else None

    close = float(current["close"])
    high = float(current["high"])
    low = float(current["low"])

    if last_high:
        level = last_high["price"]

        if high > level and close < level:
            events.append({
                "event": "BUY_SIDE_LIQUIDITY_SWEEP",
                "level": level
            })
            directional_evidence.append("BEARISH")

        elif close > level:
            events.append({
                "event": "BULLISH_STRUCTURE_BREAK",
                "level": level
            })
            directional_evidence.append("BULLISH")

    if last_low:
        level = last_low["price"]

        if low < level and close > level:
            events.append({
                "event": "SELL_SIDE_LIQUIDITY_SWEEP",
                "level": level
            })
            directional_evidence.append("BULLISH")

        elif close < level:
            events.append({
                "event": "BEARISH_STRUCTURE_BREAK",
                "level": level
            })
            directional_evidence.append("BEARISH")

    unique_directions = set(directional_evidence)

    if len(unique_directions) > 1:
        direction = "CONFLICTING"
    elif unique_directions:
        direction = next(iter(unique_directions))
    else:
        direction = "NEUTRAL"

    previous_bodies = [
        abs(float(c["close"]) - float(c["open"]))
        for c in candles[-21:-1]
    ]

    current_body = abs(
        float(current["close"]) - float(current["open"])
    )
    candle_range = high - low

    if previous_bodies and median(previous_bodies) > 0:
        if (
            current_body >= 1.5 * median(previous_bodies)
            and candle_range > 0
            and current_body / candle_range >= 0.65
        ):
            events.append({
                "event": "DISPLACEMENT",
                "direction": (
                    "BULLISH"
                    if close > float(current["open"])
                    else "BEARISH"
                )
            })

    if len(candles) >= 3:
        first = candles[-3]
        third = candles[-1]

        if float(third["low"]) > float(first["high"]):
            events.append({
                "event": "BULLISH_FVG_CANDIDATE",
                "lower": float(first["high"]),
                "upper": float(third["low"])
            })

        elif float(third["high"]) < float(first["low"]):
            events.append({
                "event": "BEARISH_FVG_CANDIDATE",
                "lower": float(third["high"]),
                "upper": float(first["low"])
            })

    return {
        "timeframe": timeframe,
        "direction": direction,
        "latest_confirmed_swing_high": last_high,
        "latest_confirmed_swing_low": last_low,
        "events": events
    }


def analyse_multi_timeframe(data):
    missing = [tf for tf in TIMEFRAMES if tf not in data]

    if missing:
        raise ValueError(f"Missing timeframes: {', '.join(missing)}")

    results = {
        tf: analyse_timeframe(tf, data[tf])
        for tf in TIMEFRAMES
    }

    directions = {
        tf: results[tf]["direction"]
        for tf in TIMEFRAMES
    }

    bullish = sum(v == "BULLISH" for v in directions.values())
    bearish = sum(v == "BEARISH" for v in directions.values())

    conflicting = sum(v == "CONFLICTING" for v in directions.values())

    if conflicting or (bullish and bearish):
        alignment = "TIMEFRAME_CONFLICT"
    elif bullish == 4:
        alignment = "FULL_BULLISH_ALIGNMENT"
    elif bearish == 4:
        alignment = "FULL_BEARISH_ALIGNMENT"
    else:
        alignment = "PARTIAL_OR_NEUTRAL"

    return {
        "instrument": "XAUUSD",
        "analysis_type": "PRELIMINARY_MULTI_TIMEFRAME_RESEARCH",
        "timeframes": results,
        "alignment": alignment,
        "direction_counts": {
            "bullish": bullish,
            "bearish": bearish,
            "neutral": 4 - bullish - bearish - conflicting,
            "conflicting": conflicting
        },
        "probability": None,
        "trade_signal": None,
        "warning": (
            "Structural observations only. Not a validated forecast "
            "or trading signal."
        )
    }
