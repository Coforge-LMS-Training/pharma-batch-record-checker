"""Generator for clean (fully compliant) executed batch records.

A clean batch executes one master: one row per master step, in step order,
breaking none of the 8 agreed rules. Defects are planted on top of clean
batches later (PHA-37). All data is synthetic.
"""

import random
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from pharma_batch_record_checker.schema import BatchRow, MasterRow

# Synthetic operator identities. The two pools don't overlap, so a critical
# step's verifier is never the person who performed it.
PERFORMERS: tuple[str, ...] = ("OP-101", "OP-108", "OP-115", "OP-123")
VERIFIERS: tuple[str, ...] = ("OP-131", "OP-142", "OP-157")

# Step i is performed STEP_INTERVAL after step i-1; a critical step is
# verified 5 to 20 whole minutes after it was performed.
STEP_INTERVAL = timedelta(minutes=30)
VERIFY_DELAY_MINUTES = (5, 20)

# Plain, factual notes. Nothing here asks a reviewer to do anything:
# instruction-like comments are planted as defects separately (PHA-37).
SIGN_OFF_COMMENTS: tuple[str, ...] = (
    "Line clear. No previous product found.",
    "Area cleaned and status label checked.",
)
STEP_COMMENTS: tuple[str, ...] = (
    "Equipment ID recorded on log sheet.",
    "Balance calibration check done before use.",
    "Reading taken as per procedure.",
)
BENIGN_COMMENTS: frozenset[str] = frozenset(SIGN_OFF_COMMENTS + STEP_COMMENTS)
STEP_COMMENT_CHANCE = 0.2


def generate_clean_batch(
    master: Sequence[MasterRow],
    batch_id: str,
    start: datetime,
    seed: int,
) -> list[BatchRow]:
    """Generate a clean executed batch for one master.

    `master` must be the rows of exactly one master (one master_record_id and
    master_version), each step once; they may come in any order and are put
    in step_seq order. `start` is when the first step is performed: a naive
    datetime on a whole minute. The same arguments always give the same rows.
    """
    _check_master(master)
    _check_start(start)
    master = sorted(master, key=lambda step: step.step_seq)
    rng = random.Random(seed)
    pins = _pick_boundary_steps(master, rng)
    return [
        _clean_row(
            step, batch_id, rng, pins.get(step.step_id), start + i * STEP_INTERVAL
        )
        for i, step in enumerate(master)
    ]


def _check_master(master: Sequence[MasterRow]) -> None:
    """A batch executes one master, each step once; refuse anything else."""
    keys = {(s.master_record_id, s.master_version) for s in master}
    if len(keys) != 1:
        raise ValueError(f"master rows must come from one master, got {sorted(keys)}")
    ids = [s.step_id for s in master]
    if duplicates := sorted({i for i in ids if ids.count(i) > 1}):
        raise ValueError(f"master has duplicate step_ids {duplicates}")


def _check_start(start: datetime) -> None:
    """Timestamps are naive and minute precision, so start must be too."""
    if start.tzinfo is not None:
        raise ValueError(f"start must have no timezone, got {start.isoformat()}")
    if start.second or start.microsecond:
        raise ValueError(f"start must be a whole minute, got {start.isoformat()}")


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
    step: MasterRow,
    batch_id: str,
    rng: random.Random,
    pin: str | None,
    performed_at: datetime,
) -> BatchRow:
    """Build the clean batch row for one master step.

    Every step gets a performer and time. Only critical steps get a second
    check: a different person, strictly later.
    """
    reading = None if step.parameter is None else _reading(step, rng, pin)
    verified_by = verified_at = None
    if step.critical:
        verified_by = rng.choice(VERIFIERS)
        verified_at = performed_at + timedelta(
            minutes=rng.randint(*VERIFY_DELAY_MINUTES)
        )
    return BatchRow(
        batch_id=batch_id,
        master_record_id=step.master_record_id,
        master_version=step.master_version,
        step_id=step.step_id,
        reading=reading,
        unit=step.unit,
        performed_by=rng.choice(PERFORMERS),
        performed_at=performed_at,
        verified_by=verified_by,
        verified_at=verified_at,
        comment=_comment(step, rng),
    )


def _comment(step: MasterRow, rng: random.Random) -> str | None:
    """A benign note: always on sign-off steps, sometimes on measured steps."""
    if step.parameter is None:
        return rng.choice(SIGN_OFF_COMMENTS)
    if rng.random() < STEP_COMMENT_CHANCE:
        return rng.choice(STEP_COMMENTS)
    return None
