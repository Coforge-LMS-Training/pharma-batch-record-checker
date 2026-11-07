"""Tests for expected_findings.csv (PHA-36). All data is synthetic.

Expected findings come only from planted defects, never from a rules engine.
"""

import pytest
from pydantic import ValidationError

from pharma_batch_record_checker.expected_findings import (
    CRITICAL,
    EXPECTED_FINDINGS_HEADER,
    NEEDS_REVIEW,
    RULE_IDS,
    ExpectedFinding,
)

# --- Slice 1: row model and constants ---------------------------------------


def test_header_is_exact_four_columns():
    """The file has exactly the four agreed columns, in order."""
    assert ",".join(EXPECTED_FINDINGS_HEADER) == "batch_id,step_id,rule_id,severity"


def test_rule_ids_are_the_eight_agreed_spellings():
    """The eight confirmed rule_id spellings (rule_version 1.0.0)."""
    assert set(RULE_IDS) == {
        "out_of_range",
        "missing_reading",
        "missing_step",
        "missing_performer",
        "missing_second_check",
        "same_person",
        "verifier_before_performer",
        "unit_mismatch",
    }
    assert len(RULE_IDS) == 8


def test_severity_values():
    """The two agreed severity values."""
    assert (CRITICAL, NEEDS_REVIEW) == ("Critical", "Needs Review")


def finding(**overrides):
    """A valid ExpectedFinding; overrides replace individual fields."""
    fields = {
        "batch_id": "T26-0001",
        "step_id": "S-GRAN-02",
        "rule_id": "unit_mismatch",
        "severity": "Needs Review",
    }
    fields.update(overrides)
    return ExpectedFinding(**fields)


def test_expected_finding_rejects_unknown_rule():
    """A misspelled rule_id is refused."""
    with pytest.raises(ValidationError):
        finding(rule_id="out_of_ranges")


def test_expected_finding_rejects_unknown_severity():
    """Only the two agreed severities are allowed."""
    with pytest.raises(ValidationError):
        finding(severity="High")


def test_expected_finding_to_csv_row():
    """A finding serializes to its four cells, in header order."""
    assert finding().to_csv_row() == {
        "batch_id": "T26-0001",
        "step_id": "S-GRAN-02",
        "rule_id": "unit_mismatch",
        "severity": "Needs Review",
    }
    assert tuple(finding().to_csv_row()) == EXPECTED_FINDINGS_HEADER
