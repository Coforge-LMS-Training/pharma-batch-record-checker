"""PHA-35 acceptance: clean executed batches break none of the 8 agreed rules.

`rule_violations` is a small reference implementation of the rules exactly as
CLAUDE.md states them. It lives in the tests only (the real rule engine is the
parser/rule-engine owner's) so generator output can be checked against the
agreed rules; PHA-36/37 can reuse it.
"""

from datetime import datetime
from decimal import Decimal

import pytest

from pharma_batch_record_checker.batches import generate_clean_batch
from pharma_batch_record_checker.masters import PLC250, SYN500, build_master
from pharma_batch_record_checker.schema import BatchRow, MasterRow


def rule_violations(master: list[MasterRow], batch: list[BatchRow]) -> list[str]:
    """Return "rule:step_id" for every broken rule (rule_version 1.0.0)."""
    found = []
    by_step = {row.step_id: row for row in batch}
    for step in master:
        row = by_step.get(step.step_id)
        if row is None:
            found.append(f"missing_step:{step.step_id}")
            continue
        unit_ok = row.unit == step.unit
        if not unit_ok:
            found.append(f"unit_mismatch:{step.step_id}")
        if step.parameter is not None and row.reading is None:
            found.append(f"missing_reading:{step.step_id}")
        # The range check is skipped when the reading is missing or the unit mismatches.
        if row.reading is not None and unit_ok:
            too_low = step.min_value is not None and row.reading < step.min_value
            too_high = step.max_value is not None and row.reading > step.max_value
            if too_low or too_high:
                found.append(f"out_of_range:{step.step_id}")
        if row.performed_by is None:
            found.append(f"missing_performer:{step.step_id}")
        if step.critical:
            if row.verified_by is None or row.verified_at is None:
                found.append(f"missing_second_check:{step.step_id}")
            elif row.verified_by == row.performed_by:
                found.append(f"same_person:{step.step_id}")
        if (
            row.performed_at is not None
            and row.verified_at is not None
            and row.verified_at < row.performed_at
        ):
            found.append(f"verifier_before_performer:{step.step_id}")
    return found


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
