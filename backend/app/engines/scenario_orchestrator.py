"""Global multi-timeframe scenario orchestration; research only."""

from app.core.universe import get_instrument
from app.engines.confirmed_signal import evaluate_confirmed_candidate
from app.engines.event_sequence import analyse_event_sequence
from app.engines.multi_timeframe import analyse_multi_timeframe

TIMEFRAMES = ("D1", "H4", "H1", "M15")
DIRECTIONS = ("BULLISH", "BEARISH")


def analyse_instrument_scenarios(instrument, candles_by_timeframe):
    if not isinstance(instrument, str) or not instrument.strip():
        raise ValueError("instrument is required")

    symbol = instrument.strip().upper()
    instrument_record = get_instrument(symbol)

    if instrument_record is None or not instrument_record.enabled:
        raise ValueError("instrument is not in the enabled market universe")

    if not isinstance(candles_by_timeframe, dict):
        raise ValueError("candles_by_timeframe must be an object")

    missing = [tf for tf in TIMEFRAMES if tf not in candles_by_timeframe]
    if missing:
        raise ValueError("missing timeframes: " + ", ".join(missing))

    mtf = analyse_multi_timeframe(candles_by_timeframe)
    sequence = analyse_event_sequence(candles_by_timeframe)

    directions = {
        tf: mtf["timeframes"][tf]["direction"]
        for tf in TIMEFRAMES
    }

    scenarios = []

    for direction in DIRECTIONS:
        stages = sequence[direction.lower()].get("stages") or {}
        opposite = "BEARISH" if direction == "BULLISH" else "BULLISH"

        htf_aligned = (
            directions["D1"] == direction
            and directions["H4"] == direction
        )

        no_conflict = (
            all(value not in (opposite, "CONFLICTING")
                for value in directions.values())
            and sequence["overall_status"]
            != "CONFLICTING_COMPLETED_SEQUENCES"
        )

        evidence = {
            # These cannot be verified from an unprovenanced candle payload.
            "data_fresh": False,
            # Unverified user candles cannot establish data eligibility.
            "market_data_verified": False,
            "licensing_approved": False,
            "session_calendar_verified": False,
            "spread_verified": False,
            "higher_timeframe_context": htf_aligned,
            "liquidity_event": bool(
                stages.get("htf_sweep") and stages.get("h1_sweep")
            ),
            "displacement": bool(stages.get("htf_displacement")),
            "structure_confirmation": bool(
                stages.get("m15_structure_shift_proxy")
            ),
            "poi_retracement": bool(stages.get("m15_poi_retracement")),
            # A structure-shift proxy is not a post-retracement entry trigger.
            "entry_trigger": False,
            "risk_reward_valid": False,
            "spread_valid": False,
            "event_risk_clear": False,
            "no_material_conflict": no_conflict,
        }

        gate = evaluate_confirmed_candidate(evidence)

        scenarios.append({
            "direction": direction,
            "sequence_status": sequence[direction.lower()]["status"],
            "status": gate.status,
            "confirmed": gate.confirmed,
            "stages": stages,
            "timeframe_directions": directions,
            "evidence": evidence,
            "gate": {
                "status": gate.status,
                "confirmed": gate.confirmed,
                "passed": list(gate.passed),
                "failed": list(gate.failed),
                "reason": gate.reason,
            },
        })

    completed = [
        item["direction"] for item in scenarios
        if item["sequence_status"] == "SEQUENCE_OBSERVED"
    ]

    selected = None

    mtf["instrument"] = symbol
    sequence["instrument"] = symbol

    return {
        "instrument": symbol,
        "asset_class": instrument_record.asset_class,
        "analysis_type": "GLOBAL_MARKET_SCENARIO_RESEARCH",
        "status": "RESEARCH_ONLY",
        "observed_sequence_directions": completed,
        "selected_direction": selected,
        "direction_selection": "NONE_RESEARCH_ONLY",
        "multi_timeframe": mtf,
        "event_sequence": sequence,
        "scenarios": scenarios,
        "data_provenance": {
            "source": "USER_SUPPLIED_CANDLES",
            "provider_verified": False,
            "freshness": "UNVERIFIED",
            "live_data_available": False,
            "licensing_status": "NOT_ESTABLISHED",
            "market_data_verified": False,
            "session_calendar_verified": False,
            "spread_verified": False,
            "signal_eligible": False,
        },
        "probability": None,
        "trade_signal": None,
        "alerts_emitted": False,
        "execution_enabled": False,
        "warning": (
            "Structural research only. A completed sequence is not "
            "a confirmed entry or validated trading forecast."
        ),
    }
