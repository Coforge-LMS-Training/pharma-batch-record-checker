"""Tests for the clean executed-batch generator (PHA-35). All data is synthetic."""

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pharma_batch_record_checker.batches import BENIGN_COMMENTS, generate_clean_batch
from pharma_batch_record_checker.masters import (
    PLC250,
    SYN500,
    ProductConfig,
    StepTemplate,
    build_master,
)

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


# --- Slice 3: performers and timing -----------------------------------------


@PRODUCTS
@SEEDS
def test_every_step_has_performer_and_time(product, seed):
    """Every step records who performed it and when."""
    _, batch = clean(product, seed)
    for row in batch:
        assert row.performed_by is not None
        assert row.performed_at is not None


@PRODUCTS
@SEEDS
def test_operator_ids_are_synthetic_op_nnn(product, seed):
    """Performers and verifiers are synthetic OP-nnn identities."""
    _, batch = clean(product, seed)
    people = {r.performed_by for r in batch} | {r.verified_by for r in batch}
    assert all(re.fullmatch(r"OP-[0-9]{3}", p) for p in people - {None})


@PRODUCTS
@SEEDS
def test_critical_steps_verified_by_someone_else_later(product, seed):
    """A critical step has a second person who signs strictly after the performer."""
    master, batch = clean(product, seed)
    for step, row in zip(master, batch, strict=True):
        if step.critical:
            assert row.verified_by is not None
            assert row.verified_by != row.performed_by
            assert row.verified_at > row.performed_at


@PRODUCTS
@SEEDS
def test_non_critical_steps_not_verified(product, seed):
    """Non-critical steps carry no second check, as in the client samples."""
    master, batch = clean(product, seed)
    for step, row in zip(master, batch, strict=True):
        if not step.critical:
            assert (row.verified_by, row.verified_at) == (None, None)


@PRODUCTS
def test_performed_times_increase_in_step_order(product):
    """Steps are performed one after another, starting at the batch start."""
    _, batch = clean(product)
    times = [r.performed_at for r in batch]
    assert times[0] == START
    assert times == sorted(times)
    assert len(set(times)) == len(times)


def test_timestamps_serialize_to_minutes():
    """Timestamps are written as YYYY-MM-DDTHH:MM, like the client samples."""
    _, batch = clean(SYN500)
    out = batch[0].to_csv_row()
    assert out["performed_at"] == "2026-10-12T07:00"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", out["verified_at"])


# --- Slice 4: benign comments -----------------------------------------------


@PRODUCTS
@SEEDS
def test_sign_off_steps_have_benign_comment(product, seed):
    """A sign-off-only step (e.g. line clearance) always carries a benign note."""
    master, batch = clean(product, seed)
    for step, row in zip(master, batch, strict=True):
        if step.parameter is None:
            assert row.comment in BENIGN_COMMENTS


@PRODUCTS
@SEEDS
def test_comments_are_benign_or_blank(product, seed):
    """Clean batches never contain anything but benign notes or blanks."""
    _, batch = clean(product, seed)
    assert all(r.comment is None or r.comment in BENIGN_COMMENTS for r in batch)


def test_some_measured_steps_have_comments():
    """Benign comments also appear on ordinary steps, not only sign-offs."""
    commented = [
        r
        for seed in range(10)
        for r in clean(SYN500, seed)[1]
        if r.comment is not None and r.reading is not None
    ]
    assert commented


# --- Review fix A: master and start checks ----------------------------------


@PRODUCTS
def test_rows_follow_step_seq_not_input_order(product):
    """Master rows in any order give the same batch, timed in step_seq order."""
    master = build_master(product)
    shuffled = list(reversed(master))
    assert generate_clean_batch(shuffled, "T26-0001", START, 3) == (
        generate_clean_batch(master, "T26-0001", START, 3)
    )


def test_clean_batch_rejects_duplicate_step_ids():
    """A repeated step would duplicate the join key, so it is refused by name."""
    master = build_master(SYN500)
    with pytest.raises(ValueError, match="S-PACK-01"):
        generate_clean_batch([*master, master[14]], "T26-0001", START, 0)


@pytest.mark.parametrize(
    "start",
    [datetime(2026, 10, 12, 7, 0, 30), datetime(2026, 10, 12, 7, 0, 0, 5)],
    ids=["seconds", "microseconds"],
)
def test_clean_batch_rejects_start_not_whole_minute(start):
    """Timestamps are minute precision, so the start must be a whole minute."""
    with pytest.raises(ValueError, match="start"):
        generate_clean_batch(build_master(SYN500), "T26-0001", start, 0)


def test_clean_batch_rejects_timezone_aware_start():
    """Timestamps have no timezone, so a timezone-aware start is refused."""
    start = datetime(2026, 10, 12, 7, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="start"):
        generate_clean_batch(build_master(SYN500), "T26-0001", start, 0)


# --- Review fix B: negative one-sided limits and pin selection --------------


def tiny_master(limits):
    """A made-up master with one measured step per limits entry, in order."""
    process = tuple(
        StepTemplate(step_id, "Testing", "Check", "Measure", "Value", "degC", False)
        for step_id in limits
    )
    config = ProductConfig(
        master_record_id="MBR-TST001",
        master_version="1",
        product_name="Testolin test product",
        batch_size="100",
        batch_size_unit="units",
        excipient="starch",
        process=process,
        limits=limits,
    )
    return build_master(config)


NEGATIVE_LIMITS = {
    "S-A": ("0", "10"),
    "S-MIN": ("-5.0", ""),  # min-only, below zero
    "S-MAX": ("", "-10"),  # max-only, below zero
}


@SEEDS
def test_negative_min_only_limit(seed):
    """A negative min-only limit reads just above the min, without crashing."""
    batch = generate_clean_batch(tiny_master(NEGATIVE_LIMITS), "T26-0001", START, seed)
    reading = next(r.reading for r in batch if r.step_id == "S-MIN")
    assert Decimal("-5.0") <= reading <= Decimal("-4.9")


@SEEDS
def test_negative_max_only_limit(seed):
    """A negative max-only limit reads within half its size below the max."""
    batch = generate_clean_batch(tiny_master(NEGATIVE_LIMITS), "T26-0001", START, seed)
    reading = next(r.reading for r in batch if r.step_id == "S-MAX")
    assert Decimal(-15) <= reading <= Decimal(-10)


@SEEDS
def test_max_boundary_found_when_only_max_step_also_has_min(seed):
    """Pins go to different steps whenever possible, so both bounds appear."""
    master = tiny_master({"S-BOTH": ("1", "2"), "S-MIN": ("5", "")})
    batch = generate_clean_batch(master, "T26-0001", START, seed)
    pairs = list(zip(master, batch, strict=True))
    assert any(s.min_value is not None and r.reading == s.min_value for s, r in pairs)
    assert any(s.max_value is not None and r.reading == s.max_value for s, r in pairs)
