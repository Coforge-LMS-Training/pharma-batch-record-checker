"""Tests for the clean executed-batch generator (PHA-35). All data is synthetic."""

from datetime import datetime
from decimal import Decimal

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


# --- Slice 2: readings ------------------------------------------------------

SEEDS = pytest.mark.parametrize("seed", range(25))


def reading_for(product, step_id, seed=0):
    """The reading a clean batch records for one step."""
    _, batch = clean(product, seed)
    (row,) = [r for r in batch if r.step_id == step_id]
    return row.reading


def decimal_places(value):
    """Number of digits after the decimal point, as written."""
    return max(0, -value.as_tuple().exponent)


@PRODUCTS
@SEEDS
def test_readings_within_inclusive_limits(product, seed):
    """Every reading is within its step's limits (inclusive, blank = unbounded)."""
    master, batch = clean(product, seed)
    for step, row in zip(master, batch, strict=True):
        if step.parameter is None:
            continue
        assert row.reading is not None
        assert step.min_value is None or row.reading >= step.min_value
        assert step.max_value is None or row.reading <= step.max_value


@SEEDS
@pytest.mark.parametrize(("step_id", "places"), [("S-DISP-01", 2), ("S-GRAN-02", 0)])
def test_readings_use_limit_precision(seed, step_id, places):
    """Readings are written to the same precision as the step's limits."""
    assert decimal_places(reading_for(SYN500, step_id, seed)) == places


@PRODUCTS
@SEEDS
def test_batch_contains_min_and_max_boundary_readings(product, seed):
    """Each batch has a reading exactly at some step's min and one at some max."""
    master, batch = clean(product, seed)
    pairs = [
        (s, r) for s, r in zip(master, batch, strict=True) if r.reading is not None
    ]
    assert any(s.min_value is not None and r.reading == s.min_value for s, r in pairs)
    assert any(s.max_value is not None and r.reading == s.max_value for s, r in pairs)


@SEEDS
def test_equal_limits_reading_is_exact(seed):
    """Capsules per bottle (30–30) always reads exactly 30."""
    assert str(reading_for(PLC250, "S-PACK-02", seed)) == "30"


@SEEDS
def test_one_sided_limits_respected(seed):
    """Max-only and min-only steps stay on the allowed side, at sensible values."""
    lod = reading_for(PLC250, "S-BLEND-02", seed)  # max 3.0 only
    yield_ = reading_for(PLC250, "S-ENC-03", seed)  # min 98.0 only
    assert Decimal("1.5") <= lod <= Decimal("3.0")
    assert Decimal("98.0") <= yield_ <= Decimal("99.9")


@PRODUCTS
def test_same_seed_same_batch(product):
    """The same seed reproduces the batch exactly."""
    assert clean(product, seed=7)[1] == clean(product, seed=7)[1]


@PRODUCTS
def test_different_seed_different_readings(product):
    """Different seeds give different readings."""
    readings = {tuple(r.reading for r in clean(product, seed)[1]) for seed in range(5)}
    assert len(readings) > 1


def test_measured_step_without_limits_is_a_clear_error():
    """A measured step with no limits has no range to read in; say which step."""
    master = build_master(SYN500)
    master[2] = master[2].model_copy(update={"min_value": None, "max_value": None})
    with pytest.raises(ValueError, match=master[2].step_id):
        generate_clean_batch(master, "T26-0001", START, seed=0)
