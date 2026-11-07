"""PHA-35 acceptance: clean executed batches break none of the 8 agreed rules.

The rules are checked with the test-only reference in `rule_oracle.py`.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from rule_oracle import rule_violations

from pharma_batch_record_checker.generator.batches import generate_clean_batch
from pharma_batch_record_checker.generator.masters import PLC250, SYN500, build_master


@pytest.mark.parametrize("product", [SYN500, PLC250], ids=lambda p: p.master_record_id)
@pytest.mark.parametrize("seed", range(25))
def test_clean_batches_break_no_rules(product, seed):
    """Every clean batch, for either product and any seed, has zero findings."""
    master = build_master(product)
    batch = generate_clean_batch(
        master, batch_id="T26-0001", start=datetime(2026, 10, 12, 7, 0), seed=seed
    )
    assert rule_violations(master, batch) == []


@pytest.mark.parametrize(
    ("step_id", "update", "expected"),
    [
        ("S-COMP-01", {"reading": Decimal(999)}, "out_of_range:S-COMP-01"),
        ("S-COMP-01", {"reading": None}, "missing_reading:S-COMP-01"),
        ("S-COMP-01", {"performed_by": None}, "missing_performer:S-COMP-01"),
        ("S-COMP-01", {"verified_by": None}, "missing_second_check:S-COMP-01"),
        ("S-COMP-01", {"unit": "g"}, "unit_mismatch:S-COMP-01"),
        (
            "S-COMP-01",
            {"verified_at": datetime(2026, 10, 12, 6, 0)},
            "verifier_before_performer:S-COMP-01",
        ),
    ],
)
def test_oracle_catches_planted_defects(step_id, update, expected):
    """Sanity check on the oracle: each rule fires on a single planted defect."""
    master = build_master(SYN500)
    batch = generate_clean_batch(master, "T26-0001", datetime(2026, 10, 12, 7, 0), 0)
    batch = [r.model_copy(update=update) if r.step_id == step_id else r for r in batch]
    assert rule_violations(master, batch) == [expected]


def test_oracle_catches_same_person_and_missing_step():
    """Same performer and verifier, and a dropped step, are both reported."""
    master = build_master(SYN500)
    batch = generate_clean_batch(master, "T26-0001", datetime(2026, 10, 12, 7, 0), 0)
    batch = [
        r.model_copy(update={"verified_by": r.performed_by})
        if r.step_id == "S-COMP-02"
        else r
        for r in batch
        if r.step_id != "S-PACK-02"
    ]
    assert rule_violations(master, batch) == [
        "same_person:S-COMP-02",
        "missing_step:S-PACK-02",
    ]


def clean_syn500():
    """The SYN500 master and a clean batch for it (seed 0)."""
    master = build_master(SYN500)
    return master, generate_clean_batch(
        master, "T26-0001", datetime(2026, 10, 12, 7, 0), 0
    )


def test_oracle_flags_duplicate_row():
    """A step recorded twice is reported, not silently collapsed."""
    master, batch = clean_syn500()
    assert rule_violations(master, [*batch, batch[3]]) == ["duplicate_row:S-GRAN-02"]


def test_oracle_flags_row_not_in_master():
    """A row for a step the master doesn't have is reported."""
    master, batch = clean_syn500()
    extra = batch[0].model_copy(update={"step_id": "S-NOPE-99"})
    assert rule_violations(master, [*batch, extra]) == ["unexpected_row:S-NOPE-99"]


def test_oracle_flags_wrong_master_key():
    """A row with the wrong master_version doesn't count as executing that step."""
    master, batch = clean_syn500()
    batch[5] = batch[5].model_copy(update={"master_version": "2"})
    assert rule_violations(master, batch) == [
        "unexpected_row:S-GRAN-04",
        "missing_step:S-GRAN-04",
    ]
