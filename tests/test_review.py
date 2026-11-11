"""Tests for step and run severity and review() (PHA-66). All data is synthetic."""

from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import get_args

import pytest

from pharma_batch_record_checker.csv_io import write_batch_csv, write_master_csv
from pharma_batch_record_checker.generator import expected_findings as generator
from pharma_batch_record_checker.generator.batches import generate_clean_batch
from pharma_batch_record_checker.generator.defects import build_dataset
from pharma_batch_record_checker.generator.masters import build_masters
from pharma_batch_record_checker.matching import MatchError
from pharma_batch_record_checker.review import ReviewRun, Severity, review
from pharma_batch_record_checker.rules import RULE_IDS
from pharma_batch_record_checker.schema import BatchRow

MASTERS = build_masters()
SYN_ROWS = [r for r in MASTERS if r.master_record_id == "MBR-SYN500"]
CRITICAL_STEPS = {r.step_id for r in SYN_ROWS if r.critical}


def clean(batch_id="B26-0413") -> list[BatchRow]:
    """A clean SYN500 v3 batch from the generator: it breaks none of the 8 rules."""
    return generate_clean_batch(SYN_ROWS, batch_id, datetime(2026, 10, 13, 7, 0), 1)


def change(rows: list[BatchRow], step_id: str, **update) -> list[BatchRow]:
    """The same rows with one step's values changed."""
    return [r.model_copy(update=update) if r.step_id == step_id else r for r in rows]


def run(tmp_path, *batches: list[BatchRow]) -> ReviewRun:
    """Write the approved masters and these batches as CSVs, then review them."""
    master_path, batch_path = tmp_path / "master.csv", tmp_path / "batch.csv"
    write_master_csv(MASTERS, master_path)
    write_batch_csv([row for batch in batches for row in batch], batch_path)
    return review(master_path, batch_path)


def rated(result: ReviewRun, batch_id="B26-0413") -> list[tuple[str, str, str]]:
    """(step_id, rule_id, step_severity) of each finding, in output order."""
    return [
        (f.step_id, f.rule_id, f.step_severity)
        for f in result.batches[batch_id].findings
    ]


def blocked(batch_id: str) -> list[BatchRow]:
    """A batch with an unknown step ID, so matching blocks it."""
    rows = clean(batch_id)
    return [*rows, rows[0].model_copy(update={"step_id": "S-ENC-02"})]


# --- Step severity --------------------------------------------------------------


def test_two_rules_on_one_step_are_two_critical_findings(tmp_path):
    """S-COAT-02 out_of_range + same_person: separate rows, both Critical."""
    batch = change(
        clean(),
        "S-COAT-02",
        reading=Decimal("3.8"),
        performed_by="OP-117",
        verified_by="OP-117",
    )
    result = run(tmp_path, batch)
    assert rated(result) == [
        ("S-COAT-02", "out_of_range", "Critical"),
        ("S-COAT-02", "same_person", "Critical"),
    ]
    assert result.batches["B26-0413"].result_category == "Critical"


@pytest.mark.parametrize(
    "step_id, reading, severity",
    [("S-DISP-02", "31.00", "Needs Review"), ("S-DISP-01", "151.00", "Critical")],
    ids=["noncritical", "critical"],
)
def test_out_of_range_alone(tmp_path, step_id, reading, severity):
    """out_of_range alone is Critical only on a critical step."""
    assert (step_id in CRITICAL_STEPS) == (severity == "Critical")
    result = run(tmp_path, change(clean(), step_id, reading=Decimal(reading)))
    assert rated(result) == [(step_id, "out_of_range", severity)]
    assert result.batches["B26-0413"].result_category == severity


def test_missing_second_check_alone_is_critical(tmp_path):
    """A critical step with no verifier is Critical on its own."""
    batch = change(clean(), "S-DISP-01", verified_by=None, verified_at=None)
    assert rated(run(tmp_path, batch)) == [
        ("S-DISP-01", "missing_second_check", "Critical")
    ]


def test_missing_critical_step_alone_is_needs_review(tmp_path):
    """missing_step on a critical step stays Needs Review (pending confirmation)."""
    assert "S-DISP-01" in CRITICAL_STEPS
    batch = [r for r in clean() if r.step_id != "S-DISP-01"]
    assert rated(run(tmp_path, batch)) == [
        ("S-DISP-01", "missing_step", "Needs Review")
    ]


def test_findings_on_a_step_are_ordered_by_rule_id(tmp_path):
    """unit_mismatch + missing_reading come out as missing_reading, unit_mismatch."""
    batch = change(clean(), "S-GRAN-03", reading=None, unit=None)
    assert rated(run(tmp_path, batch)) == [
        ("S-GRAN-03", "missing_reading", "Critical"),
        ("S-GRAN-03", "unit_mismatch", "Critical"),
    ]


# --- Batch and run categories -------------------------------------------------------


def test_clean_batch_is_no_findings(tmp_path):
    """A clean batch, and a run of only clean batches, is No Findings."""
    result = run(tmp_path, clean())
    assert result.batches["B26-0413"].findings == ()
    assert result.batches["B26-0413"].result_category == "No Findings"
    assert (result.result_category, result.complete) == ("No Findings", True)
    assert result.rule_version == "1.1.0"


def test_critical_batch_is_not_hidden_by_a_blocked_one(tmp_path):
    """One Critical batch + one blocked: the run is Critical, but incomplete."""
    critical = change(clean(), "S-DISP-01", verified_by=None, verified_at=None)
    result = run(tmp_path, critical, blocked("B26-0414"))
    assert list(result.batches) == ["B26-0413"]
    assert (result.result_category, result.complete) == ("Critical", False)


def test_blocked_batch_is_never_no_findings(tmp_path):
    """One clean + one blocked: no category for the run, and it's incomplete."""
    result = run(tmp_path, clean(), blocked("B26-0414"))
    assert "B26-0414" not in result.batches
    assert [(e.kind, e.batch_id) for e in result.errors] == [
        ("unknown_step", "B26-0414")
    ]
    assert isinstance(result.errors[0], MatchError)
    assert (result.result_category, result.complete) == (None, False)


# --- Contract with the generator -----------------------------------------------------


def test_no_rule_id_beyond_the_ten_and_severity_names_match(tmp_path):
    """Only the 10 agreed rule IDs ever appear; severity names are the generator's."""
    data = build_dataset(0)
    result = run(tmp_path, data.batches)
    assert len(set(RULE_IDS)) == 10
    assert {f.rule_id for b in result.batches.values() for f in b.findings} <= set(
        RULE_IDS
    )
    assert get_args(Severity) == get_args(generator.Severity)


@pytest.mark.parametrize("seed", [0, 1, 42])
def test_engine_matches_generator_expected_findings(tmp_path, seed):
    """Every finding and step severity equals the generator's answer key."""
    data = build_dataset(seed)
    master_path, batch_path = tmp_path / "master.csv", tmp_path / "batch.csv"
    write_master_csv(data.masters, master_path)
    write_batch_csv(data.batches, batch_path)

    result = review(master_path, batch_path)

    assert (result.errors, result.complete) == ((), True)
    engine = Counter(
        (f.batch_id, f.step_id, f.rule_id, f.step_severity)
        for b in result.batches.values()
        for f in b.findings
    )
    expected = Counter(
        (e.batch_id, e.step_id, e.rule_id, e.severity) for e in data.findings
    )
    assert engine == expected
