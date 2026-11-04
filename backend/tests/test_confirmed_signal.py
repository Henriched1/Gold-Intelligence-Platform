from app.engines.confirmed_signal import (
    REQUIRED_CHECKS,
    evaluate_confirmed_candidate,
)


def test_missing_evidence_fails_closed():
    result = evaluate_confirmed_candidate({})
    assert result.status == "WAIT"
    assert result.confirmed is False
    assert set(result.failed) == set(REQUIRED_CHECKS)


def test_every_mandatory_check_is_required():
    evidence = {key: True for key in REQUIRED_CHECKS}
    for key in REQUIRED_CHECKS:
        candidate = dict(evidence)
        candidate[key] = False
        result = evaluate_confirmed_candidate(candidate)
        assert result.confirmed is False
        assert key in result.failed


def test_only_explicit_true_values_pass():
    evidence = {key: True for key in REQUIRED_CHECKS}
    evidence["spread_valid"] = 1
    result = evaluate_confirmed_candidate(evidence)
    assert result.status == "WAIT"
    assert "spread_valid" in result.failed


def test_complete_evidence_confirms_candidate():
    evidence = {key: True for key in REQUIRED_CHECKS}
    result = evaluate_confirmed_candidate(evidence)
    assert result.status == "CONFIRMED"
    assert result.confirmed is True
    assert result.failed == ()
