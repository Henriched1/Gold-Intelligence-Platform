from statistics import mean


def analyse_structure(candles):
    if len(candles) < 12:
        return {"status": "INSUFFICIENT_DATA", "observations": []}

    previous = candles[-11:-1]
    current = candles[-1]

    previous_high = max(c["high"] for c in previous)
    previous_low = min(c["low"] for c in previous)

    average_body = mean(
        abs(c["close"] - c["open"]) for c in previous
    )
    current_body = abs(current["close"] - current["open"])

    observations = []

    if current["high"] > previous_high and current["close"] < previous_high:
        observations.append("POTENTIAL_BUY_SIDE_LIQUIDITY_SWEEP")

    if current["low"] < previous_low and current["close"] > previous_low:
        observations.append("POTENTIAL_SELL_SIDE_LIQUIDITY_SWEEP")

    if average_body > 0 and current_body >= average_body * 1.5:
        observations.append("EXPANSIVE_CANDLE_BODY")

    if current["close"] > previous_high:
        observations.append("CLOSE_ABOVE_RECENT_RANGE")

    if current["close"] < previous_low:
        observations.append("CLOSE_BELOW_RECENT_RANGE")

    if not observations:
        observations.append("NO_CLEAR_STRUCTURE_EVENT")

    return {
        "status": "ANALYSED",
        "reference_high": previous_high,
        "reference_low": previous_low,
        "average_body": average_body,
        "current_body": current_body,
        "observations": observations
    }
