from datetime import datetime, timezone

from app.engines.multi_timeframe import (
    validate_candles,
    find_confirmed_swings,
)


def _time(candle):
    value = datetime.fromisoformat(
        candle["timestamp"].replace("Z", "+00:00")
    )
    if value.tzinfo is None:
        raise ValueError("Timestamps must include a timezone.")
    return value.astimezone(timezone.utc)


def _validate(data):
    required = ("D1", "H4", "H1", "M15")

    for timeframe in required:
        if timeframe not in data:
            raise ValueError(f"Missing timeframe: {timeframe}")

        candles = data[timeframe]
        validate_candles(candles)

        times = [_time(c) for c in candles]

        if any(times[i] >= times[i + 1] for i in range(len(times) - 1)):
            raise ValueError(
                f"{timeframe} timestamps must be chronological."
            )


def _find_sweeps(candles, direction):
    swing_highs, swing_lows = find_confirmed_swings(candles)
    pivots = swing_lows if direction == "BULLISH" else swing_highs

    results = []

    for i in range(4, len(candles)):
        confirmed = [p for p in pivots if p["index"] <= i - 2]

        if not confirmed:
            continue

        level = confirmed[-1]["price"]
        candle = candles[i]

        if direction == "BULLISH":
            swept = (
                float(candle["low"]) < level
                and float(candle["close"]) > level
            )
        else:
            swept = (
                float(candle["high"]) > level
                and float(candle["close"]) < level
            )

        if swept:
            results.append({
                "index": i,
                "timestamp": _time(candle).isoformat(),
                "level": level,
                "event": (
                    "SELL_SIDE_LIQUIDITY_SWEEP"
                    if direction == "BULLISH"
                    else "BUY_SIDE_LIQUIDITY_SWEEP"
                ),
            })

    return results


def _find_displacement(candles, sweep, direction, max_bars=5):
    start = sweep["index"] + 1
    end = min(len(candles), start + max_bars)

    for i in range(start, end):
        previous = candles[max(0, i - 20):i]

        bodies = [
            abs(float(c["close"]) - float(c["open"]))
            for c in previous
        ]

        if len(bodies) < 5:
            continue

        baseline = sorted(bodies)[len(bodies) // 2]

        if baseline <= 0:
            continue

        candle = candles[i]
        opening = float(candle["open"])
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])

        body = abs(close - opening)
        candle_range = high - low

        directional = (
            close > opening if direction == "BULLISH"
            else close < opening
        )

        if (
            directional
            and body >= 1.5 * baseline
            and candle_range > 0
            and body / candle_range >= 0.65
        ):
            return {
                "index": i,
                "timestamp": _time(candle).isoformat(),
                "event": f"{direction}_DISPLACEMENT",
            }

    return None


def _find_structure_shift(candles, after_time, direction):
    swing_highs, swing_lows = find_confirmed_swings(candles)
    pivots = swing_highs if direction == "BULLISH" else swing_lows

    for i in range(4, len(candles)):
        candle = candles[i]
        timestamp = _time(candle)

        if timestamp <= after_time:
            continue

        confirmed = [p for p in pivots if p["index"] <= i - 2]

        if not confirmed:
            continue

        level = confirmed[-1]["price"]
        close = float(candle["close"])

        if direction == "BULLISH" and close > level:
            return {
                "index": i,
                "timestamp": timestamp.isoformat(),
                "level": level,
                "event": "BULLISH_STRUCTURE_SHIFT_PROXY",
            }

        if direction == "BEARISH" and close < level:
            return {
                "index": i,
                "timestamp": timestamp.isoformat(),
                "level": level,
                "event": "BEARISH_STRUCTURE_SHIFT_PROXY",
            }

    return None


def _find_fvg(candles, after_index, direction):
    for i in range(max(2, after_index + 3), len(candles)):
        first = candles[i - 2]
        third = candles[i]

        if direction == "BULLISH":
            lower = float(first["high"])
            upper = float(third["low"])

            if upper > lower:
                return {
                    "index": i,
                    "timestamp": _time(third).isoformat(),
                    "lower": lower,
                    "upper": upper,
                    "event": "BULLISH_FVG_CANDIDATE",
                }

        else:
            lower = float(third["high"])
            upper = float(first["low"])

            if upper > lower:
                return {
                    "index": i,
                    "timestamp": _time(third).isoformat(),
                    "lower": lower,
                    "upper": upper,
                    "event": "BEARISH_FVG_CANDIDATE",
                }

    return None


def _find_retracement(candles, fvg, direction):
    for i in range(fvg["index"] + 1, len(candles)):
        candle = candles[i]
        low = float(candle["low"])
        high = float(candle["high"])

        overlaps = (
            low <= fvg["upper"]
            and high >= fvg["lower"]
        )

        if not overlaps:
            continue

        close = float(candle["close"])

        if direction == "BULLISH" and close >= fvg["lower"]:
            return {
                "index": i,
                "timestamp": _time(candle).isoformat(),
                "event": "BULLISH_POI_RETRACEMENT",
            }

        if direction == "BEARISH" and close <= fvg["upper"]:
            return {
                "index": i,
                "timestamp": _time(candle).isoformat(),
                "event": "BEARISH_POI_RETRACEMENT",
            }

    return None


def _select_latest_htf_candidate(candidates):
    """Select the newest HTF sweep, regardless of displacement status."""
    if not candidates:
        return None

    return max(
        candidates,
        key=lambda item: datetime.fromisoformat(
            item["sweep"]["timestamp"]
        ).astimezone(timezone.utc),
    )


def _analyse_direction(data, direction):
    htf_candidates = []

    for timeframe in ("D1", "H4"):
        candles = data[timeframe]

        for sweep in _find_sweeps(candles, direction):
            displacement = _find_displacement(
                candles, sweep, direction
            )

            htf_candidates.append({
                "timeframe": timeframe,
                "sweep": sweep,
                "displacement": displacement,
            })

    if not htf_candidates:
        return {
            "direction": direction,
            "status": "WAITING_FOR_HTF_LIQUIDITY_SWEEP",
            "stages": {},
        }

    # Work from the latest detected HTF sweep.
    candidate = _select_latest_htf_candidate(htf_candidates)

    stages = {
        "htf_timeframe": candidate["timeframe"],
        "htf_sweep": candidate["sweep"],
        "htf_displacement": candidate["displacement"],
    }

    if not candidate["displacement"]:
        return {
            "direction": direction,
            "status": "WAITING_FOR_HTF_DISPLACEMENT",
            "stages": stages,
        }

    displacement_time = datetime.fromisoformat(
        candidate["displacement"]["timestamp"]
    )

    h1_sweeps = [
        event for event in _find_sweeps(data["H1"], direction)
        if datetime.fromisoformat(event["timestamp"]) > displacement_time
    ]

    if not h1_sweeps:
        return {
            "direction": direction,
            "status": "WAITING_FOR_H1_LIQUIDITY_SWEEP",
            "stages": stages,
        }

    h1_sweep = h1_sweeps[0]
    stages["h1_sweep"] = h1_sweep

    m15 = data["M15"]
    shift = _find_structure_shift(
        m15,
        datetime.fromisoformat(h1_sweep["timestamp"]),
        direction,
    )

    if not shift:
        return {
            "direction": direction,
            "status": "WAITING_FOR_M15_STRUCTURE_SHIFT",
            "stages": stages,
        }

    stages["m15_structure_shift_proxy"] = shift

    fvg = _find_fvg(m15, shift["index"], direction)

    if not fvg:
        return {
            "direction": direction,
            "status": "WAITING_FOR_M15_POI",
            "stages": stages,
        }

    stages["m15_fvg_candidate"] = fvg

    retracement = _find_retracement(m15, fvg, direction)

    if not retracement:
        return {
            "direction": direction,
            "status": "WAITING_FOR_POI_RETRACEMENT",
            "stages": stages,
        }

    stages["m15_poi_retracement"] = retracement

    return {
        "direction": direction,
        "status": "SEQUENCE_OBSERVED",
        "stages": stages,
    }


def analyse_event_sequence(data):
    _validate(data)

    bullish = _analyse_direction(data, "BULLISH")
    bearish = _analyse_direction(data, "BEARISH")

    complete = [
        result["direction"]
        for result in (bullish, bearish)
        if result["status"] == "SEQUENCE_OBSERVED"
    ]

    if len(complete) == 2:
        overall = "CONFLICTING_COMPLETED_SEQUENCES"
    elif len(complete) == 1:
        overall = f"{complete[0]}_SEQUENCE_OBSERVED"
    else:
        overall = "NO_COMPLETED_SEQUENCE"

    return {
        "instrument": "XAUUSD",
        "analysis_type": "RULE_BASED_EVENT_SEQUENCE_RESEARCH",
        "overall_status": overall,
        "bullish": bullish,
        "bearish": bearish,
        "probability": None,
        "trade_signal": None,
        "timestamp_basis": "CANDLE_TIMESTAMP_ASSUMED_TO_BE_CLOSE_TIME",
        "warning": (
            "A sequence observation is not a validated forecast, "
            "entry instruction, or evidence of institutional intent."
        ),
    }
