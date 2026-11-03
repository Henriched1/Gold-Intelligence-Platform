from statistics import median


def find_swing(candles, side, lookback=30):
    if len(candles) < 5:
        return None

    start = max(2, len(candles) - lookback - 2)

    for i in range(len(candles) - 3, start - 1, -1):
        left = candles[i - 2:i]
        right = candles[i + 1:i + 3]

        if side == "HIGH":
            level = candles[i]["high"]
            if all(level > c["high"] for c in left + right):
                return {"index": i, "price": level}

        if side == "LOW":
            level = candles[i]["low"]
            if all(level < c["low"] for c in left + right):
                return {"index": i, "price": level}

    return None


def analyse_market_structure(candles):
    if len(candles) < 8:
        return {
            "status": "INSUFFICIENT_DATA",
            "events": []
        }

    current = candles[-1]
    previous = candles[:-1]

    swing_high = find_swing(previous, "HIGH")
    swing_low = find_swing(previous, "LOW")

    events = []

    if swing_high:
        level = swing_high["price"]

        if current["high"] > level and current["close"] < level:
            events.append("BUY_SIDE_LIQUIDITY_SWEEP")

        if current["close"] > level:
            events.append("BULLISH_STRUCTURE_BREAK")

    if swing_low:
        level = swing_low["price"]

        if current["low"] < level and current["close"] > level:
            events.append("SELL_SIDE_LIQUIDITY_SWEEP")

        if current["close"] < level:
            events.append("BEARISH_STRUCTURE_BREAK")

    previous_bodies = [
        abs(c["close"] - c["open"])
        for c in previous[-20:]
    ]

    typical_body = median(previous_bodies)
    body = abs(current["close"] - current["open"])
    candle_range = current["high"] - current["low"]

    displacement = False

    if typical_body > 0 and candle_range > 0:
        displacement = (
            body >= typical_body * 1.5
            and body / candle_range >= 0.65
        )

    if displacement and current["close"] > current["open"]:
        events.append("BULLISH_DISPLACEMENT")

    elif displacement and current["close"] < current["open"]:
        events.append("BEARISH_DISPLACEMENT")

    if len(candles) >= 3:
        first = candles[-3]
        last = candles[-1]

        if last["low"] > first["high"]:
            events.append("BULLISH_FVG_CANDIDATE")

        if last["high"] < first["low"]:
            events.append("BEARISH_FVG_CANDIDATE")

    if not events:
        events.append("NO_CONFIRMED_STRUCTURE_EVENT")

    return {
        "status": "ANALYSED",
        "swing_high": swing_high,
        "swing_low": swing_low,
        "events": events,
        "displacement_measurement": {
            "body_size": body,
            "typical_body": typical_body,
            "body_to_range": (
                round(body / candle_range, 4)
                if candle_range else 0
            )
        }
    }
