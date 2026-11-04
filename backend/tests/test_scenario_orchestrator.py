from unittest.mock import patch

import pytest

from app.engines.scenario_orchestrator import analyse_instrument_scenarios


TIMEFRAMES = ("D1", "H4", "H1", "M15")


def candle_data():
    return {
        tf: [{"timestamp": "2026-01-01T00:00:00+00:00"}]
        for tf in TIMEFRAMES
    }


def multi_result(directions=None):
    directions = directions or {tf: "BULLISH" for tf in TIMEFRAMES}
    return {
        "instrument": "XAUUSD",
        "alignment": "FULL_BULLISH_ALIGNMENT",
        "timeframes": {
            tf: {"direction": directions[tf]} for tf in TIMEFRAMES
        },
        "probability": None,
        "trade_signal": None,
    }


def sequence_result():
    stages = {
        "htf_sweep": {"event": "SELL_SIDE_LIQUIDITY_SWEEP"},
        "h1_sweep": {"event": "SELL_SIDE_LIQUIDITY_SWEEP"},
        "htf_displacement": {"event": "BULLISH_DISPLACEMENT"},
        "m15_structure_shift_proxy": {"event": "BULLISH_STRUCTURE_SHIFT_PROXY"},
        "m15_poi_retracement": {"event": "BULLISH_POI_RETRACEMENT"},
    }
    return {
        "instrument": "XAUUSD",
        "overall_status": "BULLISH_SEQUENCE_OBSERVED",
        "bullish": {
            "status": "SEQUENCE_OBSERVED",
            "stages": stages,
        },
        "bearish": {
            "status": "WAITING_FOR_HTF_LIQUIDITY_SWEEP",
            "stages": {},
        },
    }


def run_analysis(directions=None, sequence=None):
    with patch(
        "app.engines.scenario_orchestrator.analyse_multi_timeframe",
        return_value=multi_result(directions),
    ), patch(
        "app.engines.scenario_orchestrator.analyse_event_sequence",
        return_value=sequence or sequence_result(),
    ):
        return analyse_instrument_scenarios("XAUUSD", candle_data())


def test_complete_structure_does_not_bypass_missing_confirmation_evidence():
    result = run_analysis()
    bullish = result["scenarios"][0]

    assert result["selected_direction"] is None
    assert result["observed_sequence_directions"] == ["BULLISH"]
    assert result["direction_selection"] == "NONE_RESEARCH_ONLY"
    assert bullish["sequence_status"] == "SEQUENCE_OBSERVED"
    assert bullish["status"] == "WAIT"
    assert bullish["confirmed"] is False
    assert bullish["evidence"]["liquidity_event"] is True
    assert bullish["evidence"]["displacement"] is True
    assert bullish["evidence"]["poi_retracement"] is True
    assert bullish["evidence"]["data_fresh"] is False
    assert bullish["evidence"]["entry_trigger"] is False
    assert bullish["evidence"]["risk_reward_valid"] is False
    assert bullish["evidence"]["spread_valid"] is False
    assert bullish["evidence"]["event_risk_clear"] is False
    assert result["probability"] is None
    assert result["trade_signal"] is None
    assert result["execution_enabled"] is False


def test_opposing_lower_timeframe_blocks_conflict_check():
    directions = {
        "D1": "BULLISH",
        "H4": "BULLISH",
        "H1": "BEARISH",
        "M15": "BULLISH",
    }
    result = run_analysis(directions)
    bullish = result["scenarios"][0]

    assert bullish["evidence"]["no_material_conflict"] is False
    assert "no_material_conflict" in bullish["gate"]["failed"]


def test_conflicting_completed_sequences_do_not_select_direction():
    sequence = sequence_result()
    sequence["overall_status"] = "CONFLICTING_COMPLETED_SEQUENCES"
    sequence["bearish"] = {
        "status": "SEQUENCE_OBSERVED",
        "stages": {},
    }

    result = run_analysis(sequence=sequence)

    assert result["selected_direction"] is None
    assert result["observed_sequence_directions"] == ["BULLISH", "BEARISH"]
    assert result["direction_selection"] == "NONE_RESEARCH_ONLY"
    assert result["scenarios"][0]["evidence"]["no_material_conflict"] is False


def test_unknown_instrument_is_rejected():
    with pytest.raises(ValueError):
        analyse_instrument_scenarios("FAKEUSD", candle_data())


def test_missing_timeframe_is_rejected():
    data = candle_data()
    del data["M15"]

    with pytest.raises(ValueError):
        analyse_instrument_scenarios("XAUUSD", data)
