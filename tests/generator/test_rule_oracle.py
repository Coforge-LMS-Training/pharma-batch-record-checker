"""The test-only rule oracle's version-2 rules (9 and 10), at their boundaries.

The generator tests use rule_violations to check planted data, so the
oracle itself must follow the agreed wording exactly:

- late_verification: verified more than 60 minutes after performed. Exactly 60
  passes; skipped if either time is blank; any step.
- performed_out_of_order: performed earlier than the previous master step (by
  step_seq) that exists in the batch. The same time passes; skipped if either
  time is blank and for the batch's first step; any step.
"""

from datetime import datetime, timedelta

import pytest

from pharma_batch_record_checker.generator.batches import generate_clean_batch
from pharma_batch_record_checker.generator.masters import PLC250, SYN500, build_master
from pharma_batch_record_checker.schema import BatchRow
from tests.generator.rule_oracle import rule_violations

MASTER = build_master(SYN500)
START = datetime(2026, 10, 12, 7, 0)


def clean():
    """A clean SYN500 batch as {step_id: row}, in step order."""
    rows = generate_clean_batch(MASTER, "B26-0001", START, 0)
    return {row.step_id: row for row in rows}


def changed(row, **changes):
    """row with some fields replaced, re-validated as a BatchRow."""
    return BatchRow.model_validate({**row.model_dump(), **changes})


def v2_found(rows):
    """The v2 findings the oracle reports for a batch given as {step_id: row}."""
    found = rule_violations(MASTER, list(rows.values()))
    return [
        f
        for f in found
        if f.split(":")[0] in ("late_verification", "performed_out_of_order")
    ]


@pytest.mark.parametrize("seed", [0, 1, 7])
@pytest.mark.parametrize("product", [SYN500, PLC250], ids=["tablet", "capsule"])
def test_clean_batches_break_no_v2_rule(product, seed):
    """Steps are in order and verified 5-20 minutes after, so nothing fires."""
    master = build_master(product)
    rows = generate_clean_batch(master, "X26-0001", START, seed)
    assert rule_violations(master, rows) == []


# --- Rule 9: late_verification ------------------------------------------------


@pytest.mark.parametrize(
    ("minutes", "flagged"), [(60, False), (61, True), (65, True)], ids=str
)
def test_late_verification_threshold_is_60_minutes(minutes, flagged):
    """More than 60 minutes is late; exactly 60 passes."""
    rows = clean()
    step = rows["S-COMP-01"]  # critical, has a verifier
    rows["S-COMP-01"] = changed(
        step, verified_at=step.performed_at + timedelta(minutes=minutes)
    )
    assert v2_found(rows) == (["late_verification:S-COMP-01"] if flagged else [])


def test_late_verification_applies_to_non_critical_steps():
    """Any step: a non-critical step with a late verification is flagged."""
    rows = clean()
    step = rows["S-GRAN-03"]
    rows["S-GRAN-03"] = changed(
        step, verified_by="OP-131", verified_at=step.performed_at + timedelta(hours=2)
    )
    assert v2_found(rows) == ["late_verification:S-GRAN-03"]


def test_late_verification_is_skipped_without_a_verified_time():
    """A blank verified_at can't be late (missing_second_check covers it)."""
    rows = clean()
    rows["S-COMP-01"] = changed(rows["S-COMP-01"], verified_by=None, verified_at=None)
    assert v2_found(rows) == []


# --- Rule 10: performed_out_of_order ------------------------------------------


def test_performed_before_the_previous_step_is_flagged():
    """S-GRAN-02 performed before S-GRAN-01 is out of order."""
    rows = clean()
    before = rows["S-GRAN-01"].performed_at - timedelta(minutes=20)
    rows["S-GRAN-02"] = changed(rows["S-GRAN-02"], performed_at=before)
    assert v2_found(rows) == ["performed_out_of_order:S-GRAN-02"]


def test_performed_at_the_same_time_as_the_previous_step_passes():
    """The same time is not earlier, so it passes."""
    rows = clean()
    rows["S-GRAN-02"] = changed(
        rows["S-GRAN-02"], performed_at=rows["S-GRAN-01"].performed_at
    )
    assert v2_found(rows) == []


def test_previous_step_is_the_nearest_one_present_in_the_batch():
    """With S-GRAN-01 missing, S-GRAN-02 is compared with S-DISP-02."""
    rows = clean()
    disp02 = rows["S-DISP-02"].performed_at
    del rows["S-GRAN-01"]
    rows["S-GRAN-02"] = changed(rows["S-GRAN-02"], performed_at=disp02)
    assert v2_found(rows) == []
    rows["S-GRAN-02"] = changed(
        rows["S-GRAN-02"], performed_at=disp02 - timedelta(minutes=1)
    )
    assert v2_found(rows) == ["performed_out_of_order:S-GRAN-02"]


def test_the_first_step_present_is_never_out_of_order():
    """The batch's first step (here S-DISP-02, S-DISP-01 missing) is skipped."""
    rows = clean()
    del rows["S-DISP-01"]
    rows["S-DISP-02"] = changed(
        rows["S-DISP-02"], performed_at=START - timedelta(days=1)
    )
    assert v2_found(rows) == []
