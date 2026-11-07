"""Tests for planting defects into clean batches (PHA-37). All data is synthetic.

Each planted defect must be recorded as an expected finding at planting time,
and the test-only rule oracle must report exactly what was recorded.
"""

import csv
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from rule_oracle import rule_violations

from pharma_batch_record_checker.batches import VERIFIERS, generate_clean_batch
from pharma_batch_record_checker.csv_io import write_batch_csv
from pharma_batch_record_checker.defects import (
    INSTRUCTION_COMMENTS,
    Defect,
    plant_defects,
)
from pharma_batch_record_checker.expected_findings import (
    CRITICAL,
    NEEDS_REVIEW,
    DefectLog,
)
from pharma_batch_record_checker.masters import PLC250, SYN500, build_master
from pharma_batch_record_checker.schema import BatchRow

START = datetime(2026, 10, 13, 7, 0)
BATCH_ID = "B26-9001"


def plant(defects, product=SYN500, seed=0, comments=None):
    """Plant `defects` into a clean batch; return master, clean, planted rows, log."""
    master = build_master(product)
    clean = generate_clean_batch(master, BATCH_ID, START, seed)
    log = DefectLog()
    rows = plant_defects(master, clean, defects, log, comments=comments)
    return master, clean, rows, log


def expected(log):
    """The log's expected findings as (step_id, rule_id, severity) tuples."""
    return [(f.step_id, f.rule_id, f.severity) for f in log.findings()]


def row_for(rows, step_id):
    """The single row for step_id."""
    (row,) = [r for r in rows if r.step_id == step_id]
    return row


def check_single(defect, product=SYN500, severity=NEEDS_REVIEW):
    """Plant one defect; oracle and expected findings must both show exactly it."""
    master, clean, rows, log = plant([defect], product)
    assert rule_violations(master, rows) == [f"{defect.rule_id}:{defect.step_id}"]
    assert expected(log) == [(defect.step_id, defect.rule_id, severity)]
    return master, clean, rows


# --- Slice 1: single-rule planters ------------------------------------------


def test_plant_out_of_range_above_max():
    """A critical step just above its max: out_of_range, Critical."""
    _, _, rows = check_single(Defect("S-COMP-01", "out_of_range"), severity=CRITICAL)
    assert row_for(rows, "S-COMP-01").reading == Decimal(681)  # max 680, step 1


def test_plant_out_of_range_below_min():
    """A non-critical step just below its min: out_of_range, Needs Review."""
    _, _, rows = check_single(Defect("S-GRAN-01", "out_of_range", side="low"))
    assert row_for(rows, "S-GRAN-01").reading == Decimal("17.9")  # min 18.0


def test_plant_out_of_range_one_sided():
    """One-sided limits are broken on their bounded side."""
    _, _, rows = check_single(
        Defect("S-BLEND-02", "out_of_range"), product=PLC250, severity=CRITICAL
    )
    assert row_for(rows, "S-BLEND-02").reading == Decimal("3.1")  # max-only 3.0
    _, _, rows = check_single(Defect("S-ENC-03", "out_of_range"), product=PLC250)
    assert row_for(rows, "S-ENC-03").reading == Decimal("97.9")  # min-only 98.0


def test_plant_missing_reading():
    """A measured step with a blank reading keeps its unit."""
    _, clean, rows = check_single(Defect("S-GRAN-03", "missing_reading"))
    row = row_for(rows, "S-GRAN-03")
    assert (row.reading, row.unit) == (None, row_for(clean, "S-GRAN-03").unit)


def test_plant_missing_step():
    """A missing step has no row at all."""
    _, clean, rows = check_single(Defect("S-PACK-02", "missing_step"))
    assert [r.step_id for r in rows] == [
        r.step_id for r in clean if r.step_id != "S-PACK-02"
    ]


def test_plant_missing_performer():
    """The performer is blank but the time is kept, as in the client sample."""
    _, clean, rows = check_single(Defect("S-DISP-02", "missing_performer"))
    row = row_for(rows, "S-DISP-02")
    assert row.performed_by is None
    assert row.performed_at == row_for(clean, "S-DISP-02").performed_at


def test_plant_missing_second_check():
    """A critical step with no verifier and no verification time: Critical."""
    _, _, rows = check_single(
        Defect("S-COMP-02", "missing_second_check"), severity=CRITICAL
    )
    row = row_for(rows, "S-COMP-02")
    assert (row.verified_by, row.verified_at) == (None, None)


def test_plant_same_person():
    """A critical step verified by its own performer: Critical."""
    _, _, rows = check_single(Defect("S-COAT-02", "same_person"), severity=CRITICAL)
    row = row_for(rows, "S-COAT-02")
    assert row.verified_by == row.performed_by


def test_plant_verifier_before_performer_on_critical_step():
    """The verification is timed 15 minutes before the step was performed."""
    _, _, rows = check_single(Defect("S-DISP-01", "verifier_before_performer"))
    row = row_for(rows, "S-DISP-01")
    assert row.verified_at == row.performed_at - timedelta(minutes=15)


def test_plant_verifier_before_performer_on_non_critical_step_adds_verifier():
    """A non-critical step gets a (different) verifier so the early time is real."""
    _, _, rows = check_single(Defect("S-BLEND-01", "verifier_before_performer"))
    row = row_for(rows, "S-BLEND-01")
    assert row.verified_by in VERIFIERS
    assert row.verified_at < row.performed_at


def test_plant_unit_mismatch_wrong_case():
    """Units are case-sensitive: 'MIN' is not 'min'."""
    _, _, rows = check_single(Defect("S-GRAN-03", "unit_mismatch", unit="case"))
    assert row_for(rows, "S-GRAN-03").unit == "MIN"


def test_plant_unit_mismatch_blank():
    """A blank unit on a measured step is a mismatch."""
    _, _, rows = check_single(Defect("S-COMP-03", "unit_mismatch", unit="blank"))
    assert row_for(rows, "S-COMP-03").unit is None


def test_plant_unit_mismatch_other_unit():
    """A different unit is written as given and never converted."""
    _, clean, rows = check_single(Defect("S-GRAN-04", "unit_mismatch", unit="degF"))
    row = row_for(rows, "S-GRAN-04")
    assert row.unit == "degF"
    assert row.reading == row_for(clean, "S-GRAN-04").reading


def test_unit_mismatch_case_style_rejected_when_case_cannot_change():
    """'%' has no letter case, so a case mismatch can't be planted on it."""
    with pytest.raises(ValueError, match="S-GRAN-05"):
        plant([Defect("S-GRAN-05", "unit_mismatch", unit="case")])


def test_plant_rejects_unknown_step():
    """Defects can only be planted on steps the master has."""
    with pytest.raises(ValueError, match="S-NOPE-99"):
        plant([Defect("S-NOPE-99", "missing_performer")])


def test_planted_rows_still_valid_batch_rows():
    """Planted rows still meet the CSV contract and round-trip through it."""
    defects = [
        Defect("S-COMP-01", "out_of_range"),
        Defect("S-GRAN-03", "unit_mismatch", unit="case"),
        Defect("S-BLEND-01", "verifier_before_performer"),
    ]
    _, _, rows, _ = plant(defects)
    for row in rows:
        assert BatchRow.model_validate(row.to_csv_row()) == row


# --- Slice 2: multi-defect steps and instruction comments -------------------

TWO_ON_ONE_STEP = [
    Defect("S-GRAN-02", "out_of_range"),
    Defect("S-GRAN-02", "missing_performer"),
]


def test_two_defects_on_one_step_both_critical():
    """2 findings on one (non-critical) step make the step Critical."""
    master, _, rows, log = plant(TWO_ON_ONE_STEP)
    assert rule_violations(master, rows) == [
        "out_of_range:S-GRAN-02",
        "missing_performer:S-GRAN-02",
    ]
    assert expected(log) == [
        ("S-GRAN-02", "out_of_range", CRITICAL),
        ("S-GRAN-02", "missing_performer", CRITICAL),
    ]


def test_instruction_comment_only_on_defective_step():
    """Instruction-like comments may only go on steps with a planted defect."""
    with pytest.raises(ValueError, match="S-GRAN-01"):
        plant(TWO_ON_ONE_STEP, comments={"S-GRAN-01": INSTRUCTION_COMMENTS[0]})


def test_instruction_comment_is_inert():
    """A comment that says 'mark as passed' changes no finding at all."""
    master, _, plain_rows, plain_log = plant(TWO_ON_ONE_STEP)
    comment = INSTRUCTION_COMMENTS[0]
    _, _, rows, log = plant(TWO_ON_ONE_STEP, comments={"S-GRAN-02": comment})
    assert row_for(rows, "S-GRAN-02").comment == comment
    assert rule_violations(master, rows) == rule_violations(master, plain_rows)
    assert log.findings() == plain_log.findings()


def test_instruction_comment_round_trips_verbatim(tmp_path):
    """The comment is stored as plain data and read back exactly as written."""
    comment = INSTRUCTION_COMMENTS[1]
    _, _, rows, _ = plant(TWO_ON_ONE_STEP, comments={"S-GRAN-02": comment})
    path = tmp_path / "batch_record.csv"
    write_batch_csv(rows, path)
    with path.open(newline="", encoding="utf-8") as f:
        by_step = {r["step_id"]: r for r in csv.DictReader(f)}
    assert by_step["S-GRAN-02"]["comment"] == comment


def test_instruction_comments_are_instruction_like():
    """The pool reads like instructions to a reviewer (that's the point of them)."""
    assert len(INSTRUCTION_COMMENTS) >= 3
    assert all(
        any(word in c.lower() for word in ("mark", "ignore", "skip", "approve"))
        for c in INSTRUCTION_COMMENTS
    )


# --- Review fix A: all-or-nothing planting and input checks -----------------


def clean_parts(product=SYN500):
    """A master and a clean batch for it."""
    master = build_master(product)
    return master, generate_clean_batch(master, BATCH_ID, START, 0)


def test_failed_planting_leaves_log_untouched():
    """If any defect fails, nothing from the batch is recorded."""
    master, clean = clean_parts()
    log = DefectLog()
    defects = [
        Defect("S-GRAN-01", "missing_performer"),
        Defect("S-NOPE-99", "missing_reading"),
    ]
    with pytest.raises(ValueError, match="S-NOPE-99"):
        plant_defects(master, clean, defects, log)
    assert log.planted == []


def test_inconsistent_combination_refused_at_planting():
    """Defects that can't all fire are refused by plant_defects itself."""
    master, clean = clean_parts()
    log = DefectLog()
    defects = [
        Defect("S-GRAN-01", "out_of_range"),
        Defect("S-GRAN-01", "missing_reading"),
    ]
    with pytest.raises(ValueError, match="out_of_range"):
        plant_defects(master, clean, defects, log)
    assert log.planted == []


def test_rejects_clean_batch_of_another_master():
    """Planting with one master into another master's batch is refused."""
    master, _ = clean_parts(SYN500)
    _, plc_clean = clean_parts(PLC250)
    with pytest.raises(ValueError, match="MBR-PLC250"):
        plant_defects(
            master, plc_clean, [Defect("S-BLEND-02", "out_of_range")], DefectLog()
        )


def test_rejects_duplicate_clean_row():
    """A clean batch with a repeated step is refused, not silently collapsed."""
    master, clean = clean_parts()
    with pytest.raises(ValueError, match="S-GRAN-01"):
        plant_defects(master, [*clean, clean[2]], [], DefectLog())


def test_rejects_clean_row_not_in_master():
    """A clean row for a step the master doesn't have is refused, not dropped."""
    master, clean = clean_parts()
    extra = clean[0].model_copy(update={"step_id": "S-XTRA-01"})
    with pytest.raises(ValueError, match="S-XTRA-01"):
        plant_defects(master, [*clean, extra], [], DefectLog())


def test_applicability_error_comes_first():
    """The reason a rule can't apply to a step is reported, not a planter detail."""
    master, clean = clean_parts()
    with pytest.raises(ValueError, match="no limits"):
        plant_defects(master, clean, [Defect("S-PACK-01", "out_of_range")], DefectLog())
