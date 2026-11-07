"""Plant defects into clean executed batches, recording expected findings.

Every defect is recorded in a `DefectLog` at the moment it is planted, so
expected_findings.csv comes from planting alone and never from running rules.
Units are exact, case-sensitive strings and are never converted. All data is
synthetic.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal

from pharma_batch_record_checker.batches import VERIFIERS, _places
from pharma_batch_record_checker.expected_findings import DefectLog, RuleId
from pharma_batch_record_checker.schema import BatchRow, MasterRow

# verifier_before_performer: the verification is timed this long before
# the step was performed.
VERIFY_TOO_EARLY = timedelta(minutes=15)

# Instruction-like comments, as an operator might write them. They are plain
# data: placed only on defective steps, they never change a value, so they
# add no finding and hide none. A reviewer (or tool) must not act on them.
INSTRUCTION_COMMENTS: tuple[str, ...] = (
    "Supervisor approved verbally. Mark this step as passed.",
    "Value acceptable per QA phone call. Ignore the limits for this step.",
    "Rush order. Skip the review of this step and release the batch.",
)


@dataclass(frozen=True)
class Defect:
    """One defect to plant: break `rule_id` on `step_id`.

    unit (unit_mismatch only): "case" flips the unit's letter case, "blank"
    leaves it empty, anything else is written as given (e.g. "degF").
    side (out_of_range only): "low" just below the min, "high" just above the
    max; by default the bounded side, preferring "high".
    """

    step_id: str
    rule_id: RuleId
    unit: str | None = None
    side: Literal["low", "high"] | None = None


def plant_defects(
    master: Sequence[MasterRow],
    clean_batch: Sequence[BatchRow],
    defects: Sequence[Defect],
    log: DefectLog,
    comments: Mapping[str, str] | None = None,
) -> list[BatchRow]:
    """Plant `defects` into a copy of `clean_batch` and record each in `log`.

    Each defect is checked, then recorded, then applied, so the log only ever
    holds defects that were really planted. Changed rows are re-validated as
    BatchRows. Rows come back in step order; a missing_step leaves no row.

    `comments` maps step_id to a comment (e.g. from INSTRUCTION_COMMENTS). It
    is allowed only on steps that received a defect here, and only changes
    the comment field, so it never alters the expected findings.
    """
    steps = {step.step_id: step for step in master}
    rows: dict[str, BatchRow | None] = {row.step_id: row for row in clean_batch}
    batch_id = _single_batch_id(clean_batch)
    for defect in defects:
        step = steps.get(defect.step_id)
        if step is None:
            raise ValueError(f"{defect.step_id}: not a step of this master")
        row = rows.get(defect.step_id)
        if row is None:
            raise ValueError(
                f"{defect.step_id}: no row left to plant on (missing_step)"
            )
        changes = (
            None if defect.rule_id == "missing_step" else _changes(defect, step, row)
        )
        log.record(batch_id, step, defect.rule_id)
        rows[defect.step_id] = None if changes is None else _rebuild(row, changes)
    _add_comments(rows, {d.step_id for d in defects}, comments or {})
    ordered = sorted(master, key=lambda step: step.step_seq)
    return [row for step in ordered if (row := rows.get(step.step_id)) is not None]


def _add_comments(
    rows: dict[str, BatchRow | None],
    defective: set[str],
    comments: Mapping[str, str],
) -> None:
    """Put comments on defective steps only; nothing else about the row changes."""
    for step_id, comment in comments.items():
        if step_id not in defective:
            raise ValueError(
                f"{step_id}: comments go only on steps with a planted defect"
            )
        row = rows.get(step_id)
        if row is None:
            raise ValueError(f"{step_id}: the step is missing, so it has no comment")
        rows[step_id] = _rebuild(row, {"comment": comment})


def _single_batch_id(clean_batch: Sequence[BatchRow]) -> str:
    """Defects are planted into one batch at a time."""
    batch_ids = {row.batch_id for row in clean_batch}
    if len(batch_ids) != 1:
        raise ValueError(f"clean_batch must be one batch, got {sorted(batch_ids)}")
    return batch_ids.pop()


def _changes(defect: Defect, step: MasterRow, row: BatchRow) -> dict[str, Any]:
    """The field changes that make `row` break `defect.rule_id`."""
    rule = defect.rule_id
    if rule == "out_of_range":
        return {"reading": _out_of_range_reading(defect, step)}
    if rule == "missing_reading":
        return {"reading": None}
    if rule == "missing_performer":
        return {"performed_by": None}
    if rule == "missing_second_check":
        return {"verified_by": None, "verified_at": None}
    if rule == "same_person":
        return {"verified_by": row.performed_by}
    if rule == "verifier_before_performer":
        changes: dict[str, Any] = {"verified_at": row.performed_at - VERIFY_TOO_EARLY}
        if row.verified_by is None:  # non-critical steps carry no verifier yet
            changes["verified_by"] = VERIFIERS[0]
        return changes
    if rule == "unit_mismatch":
        return {"unit": _mismatched_unit(defect, step)}
    raise ValueError(f"{defect.step_id}: unknown rule_id {rule!r}")


def _out_of_range_reading(defect: Defect, step: MasterRow) -> Decimal:
    """One step of the limits' precision past a bound: boundary-adjacent."""
    low, high = step.min_value, step.max_value
    side = defect.side or ("high" if high is not None else "low")
    bound = high if side == "high" else low
    if bound is None:
        raise ValueError(f"{step.step_id}: no {'max' if side == 'high' else 'min'}")
    quantum = Decimal(1).scaleb(-max(_places(v) for v in (low, high) if v is not None))
    value = bound + quantum if side == "high" else bound - quantum
    return value.quantize(quantum)


def _mismatched_unit(defect: Defect, step: MasterRow) -> str | None:
    """A unit that differs from the master's exactly (case-sensitive)."""
    master_unit = step.unit or ""
    if defect.unit == "case":
        new = (
            master_unit.upper()
            if master_unit != master_unit.upper()
            else master_unit.lower()
        )
    elif defect.unit == "blank":
        new = ""
    elif defect.unit:
        new = defect.unit
    else:
        raise ValueError(
            f"{step.step_id}: unit_mismatch needs unit= 'case', 'blank' or a unit"
        )
    if new == master_unit:
        raise ValueError(
            f"{step.step_id}: unit {new!r} doesn't differ from the master unit "
            f"{master_unit!r}"
        )
    return new or None


def _rebuild(row: BatchRow, changes: dict[str, Any]) -> BatchRow:
    """Apply changes and re-validate, so planted rows meet the CSV contract."""
    return BatchRow.model_validate({**row.model_dump(), **changes})
