"""Generator for clean (fully compliant) executed batch records.

A clean batch executes one master: one row per master step, in step order,
breaking none of the 8 agreed rules. Defects are planted on top of clean
batches later (PHA-37). All data is synthetic.
"""

from collections.abc import Sequence
from datetime import datetime

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
    return [_clean_row(step, batch_id) for step in master]


def _check_single_master(master: Sequence[MasterRow]) -> None:
    """A batch executes one master; refuse empty or mixed master rows."""
    keys = {(s.master_record_id, s.master_version) for s in master}
    if len(keys) != 1:
        raise ValueError(f"master rows must come from one master, got {sorted(keys)}")


def _clean_row(step: MasterRow, batch_id: str) -> BatchRow:
    """Build the clean batch row for one master step."""
    reading = None
    if step.parameter is not None:
        reading = step.min_value if step.min_value is not None else step.max_value
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
