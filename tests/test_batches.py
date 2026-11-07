"""Tests for the clean executed-batch generator (PHA-35). All data is synthetic."""

from datetime import datetime

import pytest

from pharma_batch_record_checker.batches import generate_clean_batch
from pharma_batch_record_checker.masters import PLC250, SYN500, build_master

START = datetime(2026, 10, 12, 7, 0)
PRODUCTS = pytest.mark.parametrize(
    "product", [SYN500, PLC250], ids=lambda p: p.master_record_id
)


def clean(product, seed=0, batch_id="T26-0001"):
    """Build a product's master and a clean batch for it."""
    master = build_master(product)
    return master, generate_clean_batch(master, batch_id, START, seed)


# --- Slice 1: structure -----------------------------------------------------


@PRODUCTS
def test_clean_batch_has_every_master_step_in_order(product):
    """One batch row per master step, in step_seq order."""
    master, batch = clean(product)
    assert [r.step_id for r in batch] == [s.step_id for s in master]


@PRODUCTS
def test_clean_batch_rows_carry_batch_and_master_keys(product):
    """Every row carries the batch id and the master's join key."""
    master, batch = clean(product, batch_id="T26-0042")
    for row in batch:
        assert (row.batch_id, row.master_record_id, row.master_version) == (
            "T26-0042",
            master[0].master_record_id,
            master[0].master_version,
        )


@PRODUCTS
def test_clean_batch_units_match_master(product):
    """Units are copied exactly from the master (blank for sign-off steps)."""
    master, batch = clean(product)
    assert [r.unit for r in batch] == [s.unit for s in master]


@PRODUCTS
def test_sign_off_steps_have_blank_reading_and_unit(product):
    """A sign-off-only step records who did it, but no reading or unit."""
    master, batch = clean(product)
    for step, row in zip(master, batch, strict=True):
        if step.parameter is None:
            assert (row.reading, row.unit) == (None, None)


def test_clean_batch_rejects_mixed_masters():
    """A batch executes exactly one master, so mixed master rows are refused."""
    mixed = build_master(SYN500) + build_master(PLC250)
    with pytest.raises(ValueError, match="one master"):
        generate_clean_batch(mixed, "T26-0001", START, seed=0)
