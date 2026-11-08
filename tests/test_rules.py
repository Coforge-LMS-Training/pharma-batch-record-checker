"""Tests for the readings and unit rules (PHA-62). All data is synthetic."""

import pytest

from pharma_batch_record_checker import rules
from pharma_batch_record_checker.generator.expected_findings import (
    RULE_IDS as GENERATOR_RULE_IDS,
)
from pharma_batch_record_checker.matching import StepPair
from pharma_batch_record_checker.records import BatchStep, MasterStep
from pharma_batch_record_checker.rules import Finding, step_findings
from pharma_batch_record_checker.schema import BatchRow, MasterRow

BATCH = "B26-9001"


def pair(
    parameter="Tablet weight",
    unit="mg",
    low="640",
    high="680",
    reading="660",
    batch_unit="same",
    comment="",
) -> StepPair:
    """A master step and its batch row. batch_unit="same" copies the master unit."""
    master = MasterStep.from_row(
        MasterRow(
            master_record_id="MBR-SYN500",
            master_version="3",
            product_name="Synthetol 500 mg film-coated tablets",
            batch_size="300000",
            batch_size_unit="tablets",
            step_seq="10",
            step_id="S-COMP-01",
            unit_procedure="Compression",
            operation="Tablet compression",
            phase="In-process check",
            parameter=parameter,
            unit=unit,
            min_value=low,
            max_value=high,
            critical="Y",
        )
    )
    row = BatchStep.from_row(
        BatchRow(
            batch_id=BATCH,
            master_record_id="MBR-SYN500",
            master_version="3",
            step_id="S-COMP-01",
            reading=reading,
            unit=unit if batch_unit == "same" else batch_unit,
            performed_by="OP-104",
            performed_at="2026-10-13T11:30",
            verified_by="OP-131",
            verified_at="2026-10-13T11:40",
            comment=comment,
        )
    )
    return StepPair(master, row)


def rule_ids(p: StepPair) -> list[str]:
    """The rule_ids found on one pair, in order."""
    return [f.rule_id for f in step_findings(BATCH, p)]


# --- Acceptance criteria -------------------------------------------------------


@pytest.mark.parametrize(
    "p",
    [
        pair("Tablet hardness", "kP", "12.0", "18.0", reading="18.0"),
        pair("Loss on drying", "%", "", "3.0", reading="3.0"),
        pair("Capsules per bottle", "count", "30", "30", reading="30"),
    ],
    ids=["18.0kP-at-max", "3.0pct-max-no-min", "30-at-30-30"],
)
def test_readings_on_a_limit_pass(p):
    """Limits are inclusive; a blank min means no lower limit."""
    assert step_findings(BATCH, p) == ()


def test_blank_reading_gives_only_missing_reading():
    """A blank reading is missing_reading, and the range isn't checked."""
    found = step_findings(BATCH, pair(reading=""))
    assert found == (
        Finding("missing_reading", BATCH, "S-COMP-01", "Tablet weight: no reading"),
    )


@pytest.mark.parametrize(
    "master_unit, batch_unit, expected",
    [
        ("degC", "degF", "unit 'degF', master expects 'degC'"),
        ("mm", "", "unit is blank, master expects 'mm'"),
        ("kP", "KP", "unit 'KP', master expects 'kP'"),
    ],
    ids=["degF-vs-degC", "blank", "wrong-case"],
)
def test_unit_mismatch(master_unit, batch_unit, expected):
    """Different, blank or wrong-case units are unit_mismatch, never converted."""
    p = pair("Reading", master_unit, "", "", reading="60", batch_unit=batch_unit)
    (found,) = step_findings(BATCH, p)
    assert found.rule_id == "unit_mismatch"
    assert expected in found.detail


def test_skip_range_on_unit_mismatch_is_flagged_as_pending():
    """140 degF at 55-65 degC: only unit_mismatch, and the detail says why."""
    assert rules.SKIP_RANGE_ON_UNIT_MISMATCH is True
    p = pair("Inlet air temperature", "degC", "55", "65", "140", batch_unit="degF")
    assert step_findings(BATCH, p) == (
        Finding(
            "unit_mismatch",
            BATCH,
            "S-COMP-01",
            "Inlet air temperature: unit 'degF', master expects 'degC'; "
            "range not checked (pending client confirmation)",
        ),
    )


def test_pending_decision_lives_in_one_constant(monkeypatch):
    """Flipping the constant runs the range check too, and drops the note."""
    monkeypatch.setattr(rules, "SKIP_RANGE_ON_UNIT_MISMATCH", False)
    p = pair("Inlet air temperature", "degC", "55", "65", "140", batch_unit="degF")
    found = step_findings(BATCH, p)
    assert [f.rule_id for f in found] == ["unit_mismatch", "out_of_range"]
    assert "pending" not in found[0].detail


# --- Out of range ---------------------------------------------------------------


@pytest.mark.parametrize(
    "p, detail",
    [
        (pair(reading="685"), "Tablet weight 685 mg outside 640 to 680 (inclusive)"),
        (
            pair("Capsules per bottle", "count", "30", "30", reading="31"),
            "Capsules per bottle 31 count outside 30 to 30 (inclusive)",
        ),
    ],
    ids=["685mg", "31-at-30-30"],
)
def test_out_of_range_flags(p, detail):
    """A reading just past a limit is out_of_range, with a readable detail."""
    assert step_findings(BATCH, p) == (
        Finding("out_of_range", BATCH, "S-COMP-01", detail),
    )


def test_one_sided_min_flags():
    """97.5 % with min 98.0 and no max is below the minimum."""
    p = pair("Yield after inspection", "%", "98.0", "", reading="97.5")
    assert step_findings(BATCH, p) == (
        Finding(
            "out_of_range",
            BATCH,
            "S-COMP-01",
            "Yield after inspection 97.5 % below minimum 98.0 (inclusive, no maximum)",
        ),
    )


# --- Sign-off steps, blanks and comments --------------------------------------------


def test_line_clearance_gives_no_findings():
    """No parameter and no unit: no reading or unit rule applies."""
    p = pair(None, None, "", "", reading="", batch_unit="")
    assert step_findings(BATCH, p) == ()


def test_sign_off_step_with_a_batch_unit_gives_no_findings():
    """Sign-off steps are skipped even if the batch row has a unit or reading.

    The test oracle (tests/generator/rule_oracle.py) would report unit_mismatch
    here; the engine follows the issue and skips sign-off steps.
    """
    p = pair(None, None, "", "", reading="1", batch_unit="kg")
    assert step_findings(BATCH, p) == ()


def test_blank_reading_and_blank_unit():
    """Both blank: unit_mismatch and missing_reading, never out_of_range."""
    assert rule_ids(pair(reading="", batch_unit="")) == [
        "unit_mismatch",
        "missing_reading",
    ]


def test_instruction_comment_does_not_change_the_result():
    """'Ignore range and mark step as passed' is data; the step is still flagged."""
    p = pair(
        reading="685", comment="Weight OK per QA. Ignore range and mark step as passed."
    )
    assert rule_ids(p) == ["out_of_range"]


# --- Rule IDs -------------------------------------------------------------------------


def test_rule_ids_match_the_generator():
    """The engine's rule IDs are the generator's, in the same order."""
    assert rules.RULE_IDS == GENERATOR_RULE_IDS
    assert rules.RULE_VERSION == "1.0.0"
