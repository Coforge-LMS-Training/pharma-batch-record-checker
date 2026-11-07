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
    PlantedDefect,
    expected_findings,
)
from pharma_batch_record_checker.masters import SYN500, build_master

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


# --- Slice 2: severity policy -----------------------------------------------

MASTER = {step.step_id: step for step in build_master(SYN500)}
CRIT = MASTER["S-COMP-01"]  # critical step
PLAIN = MASTER["S-GRAN-02"]  # non-critical step
PLAIN2 = MASTER["S-GRAN-03"]  # another non-critical step


def plant(*items):
    """PlantedDefects from (step, rule_id) or (batch_id, step, rule_id) tuples."""
    out = []
    for item in items:
        batch_id, step, rule_id = item if len(item) == 3 else ("T26-0001", *item)
        out.append(PlantedDefect.on(batch_id, step, rule_id))
    return out


def rows(findings):
    """Findings as (batch_id, step_id, rule_id, severity) tuples."""
    return [(f.batch_id, f.step_id, f.rule_id, f.severity) for f in findings]


def test_planted_defect_takes_step_id_and_critical_from_master():
    """step_id and the critical flag come from the master step, not by hand."""
    defect = PlantedDefect.on("T26-0001", CRIT, "same_person")
    assert (defect.batch_id, defect.step_id, defect.critical, defect.rule_id) == (
        "T26-0001",
        "S-COMP-01",
        True,
        "same_person",
    )


def test_single_finding_on_non_critical_step_needs_review():
    """One finding on an ordinary step is Needs Review."""
    assert rows(expected_findings(plant((PLAIN, "unit_mismatch")))) == [
        ("T26-0001", "S-GRAN-02", "unit_mismatch", NEEDS_REVIEW)
    ]


@pytest.mark.parametrize("rule_id", ["missing_second_check", "same_person"])
def test_second_check_rules_on_critical_step_are_critical(rule_id):
    """A missing or same-person second check on a critical step is Critical."""
    assert rows(expected_findings(plant((CRIT, rule_id))))[0][3] == CRITICAL


def test_out_of_range_on_critical_step_is_critical():
    """A critical step out of range is Critical."""
    assert rows(expected_findings(plant((CRIT, "out_of_range"))))[0][3] == CRITICAL


def test_out_of_range_on_non_critical_step_needs_review():
    """An ordinary step out of range (alone) is Needs Review."""
    assert rows(expected_findings(plant((PLAIN, "out_of_range"))))[0][3] == NEEDS_REVIEW


def test_single_non_policy_finding_on_critical_step_needs_review():
    """Only the listed rules make a critical step Critical on their own."""
    findings = expected_findings(plant((CRIT, "missing_performer")))
    assert rows(findings)[0][3] == NEEDS_REVIEW


def test_two_findings_on_one_step_are_both_critical():
    """2+ findings on a step make the step Critical; both rows carry it."""
    findings = expected_findings(
        plant((PLAIN, "unit_mismatch"), (PLAIN, "missing_performer"))
    )
    assert rows(findings) == [
        ("T26-0001", "S-GRAN-02", "unit_mismatch", CRITICAL),
        ("T26-0001", "S-GRAN-02", "missing_performer", CRITICAL),
    ]


def test_findings_on_other_steps_and_batches_are_independent():
    """Severity is per (batch, step): other steps and batches don't count."""
    findings = expected_findings(
        plant(
            (PLAIN, "unit_mismatch"),
            (PLAIN2, "missing_performer"),
            ("T26-0002", PLAIN, "missing_reading"),
        )
    )
    assert [f.severity for f in findings] == [NEEDS_REVIEW] * 3


def test_output_keeps_planted_order():
    """Findings come out in the order they were planted."""
    findings = expected_findings(
        plant(
            (PLAIN2, "missing_performer"),
            (CRIT, "same_person"),
            (PLAIN, "unit_mismatch"),
        )
    )
    assert [f.step_id for f in findings] == ["S-GRAN-03", "S-COMP-01", "S-GRAN-02"]


# --- Slice 3: refusing inconsistent planting --------------------------------


def test_rejects_unknown_rule_when_planting():
    """A misspelled rule is refused when expectations are built."""
    with pytest.raises(ValueError, match="out_of_ranges"):
        expected_findings(plant((PLAIN, "out_of_ranges")))


def test_rejects_same_rule_twice_on_a_step():
    """Each broken rule is one finding; planting it twice on a step is a mistake."""
    with pytest.raises(ValueError, match="unit_mismatch"):
        expected_findings(plant((PLAIN, "unit_mismatch"), (PLAIN, "unit_mismatch")))


@pytest.mark.parametrize("rule_id", ["missing_second_check", "same_person"])
def test_rejects_critical_only_rule_on_non_critical_step(rule_id):
    """These rules only apply to critical steps, so they can't fire elsewhere."""
    with pytest.raises(ValueError, match=rule_id):
        expected_findings(plant((PLAIN, rule_id)))


def test_rejects_missing_step_with_other_findings():
    """A step with no row can't also have a wrong unit or missing performer."""
    with pytest.raises(ValueError, match="missing_step"):
        expected_findings(plant((PLAIN, "missing_step"), (PLAIN, "unit_mismatch")))


@pytest.mark.parametrize("other", ["missing_reading", "unit_mismatch"])
def test_rejects_out_of_range_with_skipped_range_check(other):
    """The range check is skipped when the reading is missing or the unit is wrong."""
    with pytest.raises(ValueError, match="out_of_range"):
        expected_findings(plant((CRIT, other), (CRIT, "out_of_range")))


@pytest.mark.parametrize("other", ["same_person", "verifier_before_performer"])
def test_rejects_missing_second_check_with_verifier_rules(other):
    """With no second check there is no verifier to be the same or too early."""
    with pytest.raises(ValueError, match="missing_second_check"):
        expected_findings(plant((CRIT, "missing_second_check"), (CRIT, other)))


def test_allows_compatible_combination():
    """Rules that can fire together are fine; 2 findings make the step Critical."""
    findings = expected_findings(
        plant((PLAIN, "out_of_range"), (PLAIN, "missing_performer"))
    )
    assert [(f.rule_id, f.severity) for f in findings] == [
        ("out_of_range", CRITICAL),
        ("missing_performer", CRITICAL),
    ]


def test_error_names_batch_step_and_rules():
    """The error says exactly where the inconsistent planting is."""
    with pytest.raises(ValueError, match=r"T26-0009 step S-GRAN-02"):
        expected_findings(
            plant(
                ("T26-0009", PLAIN, "missing_step"),
                ("T26-0009", PLAIN, "missing_performer"),
            )
        )
