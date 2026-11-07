"""Generator for clean (fully compliant) executed batch records.

A clean batch executes one master: one row per master step, in step order,
breaking none of the 8 agreed rules. Defects are planted on top of clean
batches later (PHA-37). All data is synthetic.
"""

import random
from collections.abc import Sequence
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from pharma_batch_record_checker.schema import BatchRow, MasterRow


def generate_clean_batch(
    master: Sequence[MasterRow],
    batch_id: str,
    start: datetime,
    seed: int,
) -> list[BatchRow]:
    """Generate a clean executed batch for one master.

    `master` must be the rows of exactly one master (one master_record_id and
    master_version). The same arguments always give the same rows.
    """
    _check_single_master(master)
    rng = random.Random(seed)
    pins = _pick_boundary_steps(master, rng)
    return [_clean_row(step, batch_id, rng, pins.get(step.step_id)) for step in master]


def _check_single_master(master: Sequence[MasterRow]) -> None:
    """A batch executes one master; refuse empty or mixed master rows."""
    keys = {(s.master_record_id, s.master_version) for s in master}
    if len(keys) != 1:
        raise ValueError(f"master rows must come from one master, got {sorted(keys)}")


def _pick_boundary_steps(master: Sequence[MasterRow], rng: random.Random) -> dict:
    """Pick one measured step to read exactly its min and another its max.

    This guarantees every clean batch contains boundary readings, which must
    pass because limits are inclusive.
    """
    with_min = [s.step_id for s in master if s.parameter and s.min_value is not None]
    with_max = [s.step_id for s in master if s.parameter and s.max_value is not None]
    pins = {}
    if with_min:
        pins[rng.choice(with_min)] = "min"
    others = [step_id for step_id in with_max if step_id not in pins]
    if others:
        pins[rng.choice(others)] = "max"
    return pins


def _reading(step: MasterRow, rng: random.Random, pin: str | None) -> Decimal:
    """A reading inside the step's limits, at the limits' precision.

    Two-sided limits: anywhere on the grid from min to max, ends included.
    One-sided limits get a plausible far side: min-only reads up to 2% above
    the min, max-only reads from half the max up to the max.
    """
    low, high = step.min_value, step.max_value
    if low is None and high is None:
        raise ValueError(
            f"{step.step_id}: a measured step needs at least one limit "
            "to generate a clean reading"
        )
    places = max(_places(v) for v in (low, high) if v is not None)
    quantum = Decimal(1).scaleb(-places)
    if pin == "min":
        return low.quantize(quantum)
    if pin == "max":
        return high.quantize(quantum)
    if high is None:
        high = low * Decimal("1.02")
    if low is None:
        low = high * Decimal("0.5")
    low = low.quantize(quantum, rounding=ROUND_CEILING)
    high = high.quantize(quantum, rounding=ROUND_FLOOR)
    steps = int((high - low) / quantum)
    return (low + rng.randint(0, steps) * quantum).quantize(quantum)


def _places(value: Decimal) -> int:
    """Digits after the decimal point, as written ("149.25" -> 2, "150" -> 0)."""
    return max(0, -value.as_tuple().exponent)


def _clean_row(
    step: MasterRow, batch_id: str, rng: random.Random, pin: str | None
) -> BatchRow:
    """Build the clean batch row for one master step."""
    reading = None if step.parameter is None else _reading(step, rng, pin)
    return BatchRow(
        batch_id=batch_id,
        master_record_id=step.master_record_id,
        master_version=step.master_version,
        step_id=step.step_id,
        reading=reading,
        unit=step.unit,
        performed_by=None,
        performed_at=None,
        verified_by=None,
        verified_at=None,
        comment=None,
    )
