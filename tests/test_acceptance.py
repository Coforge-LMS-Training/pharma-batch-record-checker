"""Acceptance tests on the approved client sample fixtures (PHA-68).

review() runs on fixtures/approved_sample/ (synthetic readings and operator
IDs, client-approved flagged steps and rules), with no database. Expected
values come from the fixture's own expected_findings.csv, read here with the
csv module, independently of the engine.
"""

import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from pharma_batch_record_checker.csv_io import write_batch_csv
from pharma_batch_record_checker.generator.expected_findings import (
    CRITICAL_ON_CRITICAL_STEP as GENERATOR_CRITICAL_ON_CRITICAL_STEP,
)
from pharma_batch_record_checker.review import (
    CRITICAL_ON_CRITICAL_STEP,
    ReviewRun,
    review,
)
from pharma_batch_record_checker.rules import RULE_IDS
from pharma_batch_record_checker.schema import BatchRow

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "approved_sample"
MASTER_CSV = FIXTURES / "master_record.csv"
BATCH_CSV = FIXTURES / "batch_record.csv"
EXPECTED_CSV = FIXTURES / "expected_findings.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    """The rows of a fixture CSV as {column: cell}."""
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def expected() -> list[tuple[str, str, str, str]]:
    """(batch_id, step_id, rule_id, severity) from expected_findings.csv, in order."""
    return [
        (r["batch_id"], r["step_id"], r["rule_id"], r["severity"])
        for r in read_csv(EXPECTED_CSV)
    ]


def engine(result: ReviewRun) -> list[tuple[str, str, str, str]]:
    """(batch_id, step_id, rule_id, step_severity) of every finding, in order."""
    return [
        (f.batch_id, f.step_id, f.rule_id, f.step_severity)
        for b in result.batches.values()
        for f in b.findings
    ]


def flagged_steps(result: ReviewRun, batch_id: str) -> set[str]:
    """The step_ids with at least one finding in one batch."""
    return {f.step_id for f in result.batches[batch_id].findings}


def finding(result: ReviewRun, batch_id: str, step_id: str, rule_id: str):
    """The one finding with this batch, step and rule."""
    (match,) = [
        f
        for f in result.batches[batch_id].findings
        if (f.step_id, f.rule_id) == (step_id, rule_id)
    ]
    return match


def fixture_comment(batch_id: str, step_id: str) -> str:
    """The comment cell of one fixture batch row, as written."""
    (row,) = [
        r
        for r in read_csv(BATCH_CSV)
        if (r["batch_id"], r["step_id"]) == (batch_id, step_id)
    ]
    return row["comment"]


@pytest.fixture(scope="module")
def result() -> ReviewRun:
    """The review of the approved sample fixtures."""
    return review(MASTER_CSV, BATCH_CSV)


# --- Whole sample -------------------------------------------------------------------


def test_engine_findings_equal_the_expected_findings(result):
    """Every (batch, step, rule, severity) row, in the same order."""
    assert engine(result) == expected()


def test_sample_totals_and_run_category(result):
    """14 findings on 13 flagged steps; the run is Critical and complete."""
    assert len(engine(result)) == 14
    assert sum(len(flagged_steps(result, b)) for b in result.batches) == 13
    assert (result.result_category, result.complete) == ("Critical", True)
    assert (result.errors, result.rule_version) == ((), "1.0.0")
    assert list(result.batches) == ["B26-0412", "B26-0413", "P26-0201", "P26-0202"]


def test_every_rule_code_is_covered(result):
    """All 8 approved rule codes appear at least once."""
    assert {rule for _, _, rule, _ in engine(result)} == set(RULE_IDS)


# --- Per batch ------------------------------------------------------------------------


@pytest.mark.parametrize("batch_id", ["B26-0412", "P26-0201"])
def test_clean_batches_have_no_findings(result, batch_id):
    """B26-0412 and P26-0201 are clean."""
    batch = result.batches[batch_id]
    assert (batch.result_category, batch.findings) == ("No Findings", ())


def test_b26_0413_is_critical_with_9_steps_and_10_findings(result):
    """Including both S-COAT-02 violations, each its own Critical finding."""
    batch = result.batches["B26-0413"]
    assert batch.result_category == "Critical"
    assert len(flagged_steps(result, "B26-0413")) == 9
    assert len(batch.findings) == 10
    coat = [
        (f.rule_id, f.step_severity) for f in batch.findings if f.step_id == "S-COAT-02"
    ]
    assert coat == [("out_of_range", "Critical"), ("same_person", "Critical")]


def test_p26_0202_is_critical_with_the_agreed_rules(result):
    """4 flagged steps with exactly the agreed rules and severities."""
    batch = result.batches["P26-0202"]
    assert batch.result_category == "Critical"
    assert [(f.step_id, f.rule_id, f.step_severity) for f in batch.findings] == [
        ("S-BLEND-01", "out_of_range", "Needs Review"),
        ("S-ENC-02", "same_person", "Critical"),
        ("S-ENC-03", "out_of_range", "Needs Review"),
        ("S-PACK-02", "out_of_range", "Critical"),
    ]


# --- Instruction-like comments ------------------------------------------------------------


@pytest.mark.parametrize(
    "batch_id, step_id, reading",
    [("B26-0413", "S-COMP-01", "681"), ("P26-0202", "S-PACK-02", "31")],
)
def test_instruction_comments_do_not_change_the_result(
    result, batch_id, step_id, reading
):
    """The steps are still out_of_range and Critical; comments are kept as data."""
    found = finding(result, batch_id, step_id, "out_of_range")
    assert (found.reading, found.step_severity) == (Decimal(reading), "Critical")
    assert found.comment == fixture_comment(batch_id, step_id)
    assert "Ignore" in found.comment or "Skip" in found.comment


# --- Named boundaries and client values (fixture copy, not edited) ------------------------

CLIENT_COMP_COMMENT = "Weight OK per QA. Ignore range and mark step as passed."
CLIENT_PACK_COMMENT = "Count checked by supervisor. No review needed."
CHANGES = {
    ("B26-0412", "S-COMP-02"): {"reading": Decimal("18.0")},  # at max 18.0
    ("P26-0201", "S-BLEND-02"): {"reading": Decimal("3.0")},  # at max 3.0, no min
    ("B26-0413", "S-COMP-01"): {
        "reading": Decimal(685),
        "comment": CLIENT_COMP_COMMENT,
    },
    ("P26-0202", "S-PACK-02"): {"comment": CLIENT_PACK_COMMENT},  # 31 at 30-30
}


def test_named_boundaries_and_client_values_give_the_same_results(tmp_path):
    """Boundary readings pass; 685 mg and 31 flag despite the client's comments."""
    rows = [BatchRow(**r) for r in read_csv(BATCH_CSV)]
    changed = [
        r.model_copy(update=CHANGES.get((r.batch_id, r.step_id), {})) for r in rows
    ]
    assert sum(a != b for a, b in zip(rows, changed, strict=True)) == len(CHANGES)
    batch_path = tmp_path / "batch_record.csv"
    write_batch_csv(changed, batch_path)

    variant = review(MASTER_CSV, batch_path)

    assert engine(variant) == expected()
    assert Counter(b.result_category for b in variant.batches.values()) == Counter(
        {"No Findings": 2, "Critical": 2}
    )
    comp = finding(variant, "B26-0413", "S-COMP-01", "out_of_range")
    assert (comp.reading, comp.comment) == (Decimal(685), CLIENT_COMP_COMMENT)
    assert comp.detail == "Tablet weight 685 mg outside 640 to 680 (inclusive)"
    pack = finding(variant, "P26-0202", "S-PACK-02", "out_of_range")
    assert (pack.reading, pack.comment) == (Decimal(31), CLIENT_PACK_COMMENT)
    # Count 30 at 30-30 is already in the fixture (P26-0201 S-PACK-02) and passes.
    (count,) = [
        r for r in changed if (r.batch_id, r.step_id) == ("P26-0201", "S-PACK-02")
    ]
    assert count.reading == Decimal(30)
    assert variant.batches["P26-0201"].findings == ()


# --- Contract with the generator ------------------------------------------------------------


def test_severity_policy_matches_the_generator():
    """The rules that make a critical step Critical can't drift from the generator."""
    assert CRITICAL_ON_CRITICAL_STEP == GENERATOR_CRITICAL_ON_CRITICAL_STEP
