"""Fail-closed gate for confirmed-only research alerts.

This validates upstream evidence; it does not independently detect or
predict a trade. Missing evidence always results in WAIT.
"""
from dataclasses import dataclass


REQUIRED_CHECKS = (
    "data_fresh",
    "market_data_verified",
    "licensing_approved",
    "session_calendar_verified",
    "spread_verified",
    "higher_timeframe_context",
    "liquidity_event",
    "displacement",
    "structure_confirmation",
    "poi_retracement",
    "entry_trigger",
    "risk_reward_valid",
    "spread_valid",
    "event_risk_clear",
    "no_material_conflict",
)


@dataclass(frozen=True)
class GateResult:
    status: str
    confirmed: bool
    passed: tuple[str, ...]
    failed: tuple[str, ...]
    reason: str


def evaluate_confirmed_candidate(evidence: dict) -> GateResult:
    """Confirm only if every mandatory check is explicitly boolean True."""
    passed = tuple(key for key in REQUIRED_CHECKS if evidence.get(key) is True)
    failed = tuple(key for key in REQUIRED_CHECKS if evidence.get(key) is not True)
    if failed:
        return GateResult(
            status="WAIT",
            confirmed=False,
            passed=passed,
            failed=failed,
            reason="Mandatory evidence is missing or failed; no signal is issued.",
        )
    return GateResult(
        status="CONFIRMED",
        confirmed=True,
        passed=passed,
        failed=(),
        reason="All mandatory evidence checks passed; research alert may be issued.",
    )
