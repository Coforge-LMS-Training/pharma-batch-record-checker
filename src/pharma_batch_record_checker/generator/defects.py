"""Plant defects into clean executed batches, recording expected findings.

Every defect is recorded in a `DefectLog` at the moment it is planted, so
expected_findings.csv comes from planting alone and never from running rules.
Units are exact, case-sensitive strings and are never converted. All data is
synthetic.
"""

import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal

from pharma_batch_record_checker.generator.batches import (
    VERIFIERS,
    generate_clean_batch,
    limit_quantum,
    repeated,
)
from pharma_batch_record_checker.generator.expected_findings import (
    DefectLog,
    ExpectedFinding,
    RuleId,
)
from pharma_batch_record_checker.generator.masters import (
    PLC250,
    SYN500,
    ProductConfig,
    build_master,
)
from pharma_batch_record_checker.schema import BatchRow, MasterRow

# verifier_before_performer: the verification is timed this long before
# the step was performed.
VERIFY_TOO_EARLY = timedelta(minutes=15)
# late_verification (v2 rule 9): verified more than LATE_VERIFICATION_MINUTES
# after performing. Planted at 65 minutes, the spec's example.
LATE_VERIFICATION_MINUTES = 60
VERIFY_LATE = timedelta(minutes=65)
# performed_out_of_order (v2 rule 10): performed this long before the previous
# step present in the batch (the spec's 08:00 / 07:40 example).
OUT_OF_ORDER_GAP = timedelta(minutes=20)

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

    def __post_init__(self) -> None:
        """Refuse options that the rule would silently ignore."""
        if self.unit is not None and self.rule_id != "unit_mismatch":
            raise ValueError(
                f"{self.step_id}: unit= only applies to unit_mismatch, "
                f"not {self.rule_id}"
            )
        if self.side is not None and self.rule_id != "out_of_range":
            raise ValueError(
                f"{self.step_id}: side= only applies to out_of_range, "
                f"not {self.rule_id}"
            )


def plant_defects(
    master: Sequence[MasterRow],
    clean_batch: Sequence[BatchRow],
    defects: Sequence[Defect],
    log: DefectLog,
    comments: Mapping[str, str] | None = None,
) -> list[BatchRow]:
    """Plant `defects` into a copy of `clean_batch` and record each in `log`.

    Planting is all-or-nothing. Each defect is first recorded in a private
    staging log (which refuses a rule that can't apply to its step), then
    applied to its row. Once every defect is planted, the staged findings
    are built (which refuses combinations that couldn't all fire), and only
    then are the defects recorded in `log`. If anything fails, `log` is
    unchanged. Changed rows are re-validated as BatchRows. Rows come back in
    step order; a missing_step leaves no row.

    `comments` maps step_id to a comment (e.g. from INSTRUCTION_COMMENTS). It
    is allowed only on steps that received a defect here, and only changes
    the comment field, so it never alters the expected findings.
    """
    steps = {step.step_id: step for step in master}
    batch_id = _check_clean_batch(master, clean_batch)
    rows: dict[str, BatchRow | None] = {row.step_id: row for row in clean_batch}
    staged = DefectLog()
    planted: list[tuple[MasterRow, str]] = []
    out_of_order: list[MasterRow] = []  # planted last, against the final rows
    for defect in defects:
        step = steps.get(defect.step_id)
        if step is None:
            raise ValueError(f"{defect.step_id}: not a step of this master")
        row = rows.get(defect.step_id)
        if row is None:
            raise ValueError(
                f"{defect.step_id}: no row left to plant on (missing_step)"
            )
        staged.record(batch_id, step, defect.rule_id)  # applicability first
        if defect.rule_id == "missing_step":
            rows[defect.step_id] = None
        elif defect.rule_id == "performed_out_of_order":
            out_of_order.append(step)
        else:
            rows[defect.step_id] = _rebuild(row, _changes(defect, step, row))
        planted.append((step, defect.rule_id))
    for step in sorted(out_of_order, key=lambda s: s.step_seq):
        _plant_out_of_order(master, rows, step)
    _add_comments(rows, {d.step_id for d in defects}, comments or {})
    staged.findings()  # refuses combinations that couldn't all fire
    for step, rule_id in planted:
        log.record(batch_id, step, rule_id)
    ordered = sorted(master, key=lambda step: step.step_seq)
    return [row for step in ordered if (row := rows.get(step.step_id)) is not None]


def _plant_out_of_order(
    master: Sequence[MasterRow], rows: dict[str, BatchRow | None], step: MasterRow
) -> None:
    """Move step's row to OUT_OF_ORDER_GAP before the previous step present.

    "Previous" is the nearest earlier master step (by step_seq) whose row is
    still in the batch, as rule 10 defines it. The verification moves by the
    same amount, so the verify delay is unchanged and no late or early
    verification finding appears as a side effect.
    """
    row = rows.get(step.step_id)
    if row is None:
        raise ValueError(f"{step.step_id}: no row left to plant on (missing_step)")
    earlier = [
        r
        for s in sorted(master, key=lambda s: s.step_seq)
        if s.step_seq < step.step_seq and (r := rows.get(s.step_id)) is not None
    ]
    if not earlier:
        raise ValueError(f"{step.step_id}: no earlier step in the batch to be before")
    previous = earlier[-1]
    if row.performed_at is None or previous.performed_at is None:
        raise ValueError(f"{step.step_id}: performed_out_of_order needs both times")
    moved = previous.performed_at - OUT_OF_ORDER_GAP
    shift = moved - row.performed_at
    changes: dict[str, Any] = {"performed_at": moved}
    if row.verified_at is not None:
        changes["verified_at"] = row.verified_at + shift
    rows[step.step_id] = _rebuild(row, changes)


def _check_clean_batch(
    master: Sequence[MasterRow], clean_batch: Sequence[BatchRow]
) -> str:
    """The clean batch must be one batch of this master, every step exactly once.

    Returns its batch_id.
    """
    batch_ids = {row.batch_id for row in clean_batch}
    if len(batch_ids) != 1:
        raise ValueError(f"clean_batch must be one batch, got {sorted(batch_ids)}")
    master_keys = {(s.master_record_id, s.master_version) for s in master}
    batch_keys = {(r.master_record_id, r.master_version) for r in clean_batch}
    if batch_keys != master_keys:
        raise ValueError(
            f"clean batch executes {sorted(batch_keys)}, not master {sorted(master_keys)}"
        )
    step_ids = [row.step_id for row in clean_batch]
    if duplicates := repeated(step_ids):
        raise ValueError(f"clean batch repeats steps {duplicates}")
    if unknown := sorted(set(step_ids) - {s.step_id for s in master}):
        raise ValueError(f"clean batch has steps not in the master {unknown}")
    if missing := sorted({s.step_id for s in master} - set(step_ids)):
        raise ValueError(
            f"clean batch lacks master steps {missing}; plant missing_step instead"
        )
    return batch_ids.pop()


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
        if row.performed_by is None:
            raise ValueError(f"{defect.step_id}: same_person has no performer to copy")
        return {"verified_by": row.performed_by}
    if rule == "verifier_before_performer":
        changes: dict[str, Any] = {"verified_at": row.performed_at - VERIFY_TOO_EARLY}
        if row.verified_by is None:  # non-critical steps carry no verifier yet
            changes["verified_by"] = VERIFIERS[0]
        return changes
    if rule == "unit_mismatch":
        return {"unit": _mismatched_unit(defect, step)}
    if rule == "late_verification":
        late: dict[str, Any] = {"verified_at": row.performed_at + VERIFY_LATE}
        if row.verified_by is None:  # non-critical steps carry no verifier yet
            late["verified_by"] = VERIFIERS[0]
        return late
    raise ValueError(f"{defect.step_id}: unknown rule_id {rule!r}")


def _out_of_range_reading(defect: Defect, step: MasterRow) -> Decimal:
    """One step of the limits' precision past a bound: boundary-adjacent."""
    low, high = step.min_value, step.max_value
    side = defect.side or ("high" if high is not None else "low")
    bound = high if side == "high" else low
    if bound is None:
        raise ValueError(f"{step.step_id}: no {'max' if side == 'high' else 'min'}")
    quantum = limit_quantum(step)
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


# --- The dataset ----------------------------------------------------------------


@dataclass(frozen=True)
class BatchPlan:
    """One batch in the dataset: a clean batch, optionally with planted defects."""

    product: ProductConfig
    batch_id: str
    start: datetime
    defects: tuple[Defect, ...] = ()
    comments: Mapping[str, str] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        """Keep a read-only copy of comments, so plans can't change in place."""
        object.__setattr__(self, "comments", MappingProxyType(dict(self.comments)))


# Clean batches, then one defect batch per product. Together the defect batches
# cover all 8 rules, critical and non-critical steps, one-sided limits, blank /
# wrong-case / different units, two-defect steps, and instruction-like comments
# on defective steps only.
BATCH_PLANS: tuple[BatchPlan, ...] = (
    BatchPlan(SYN500, "B26-0001", datetime(2026, 10, 12, 7, 0)),
    BatchPlan(PLC250, "P26-0001", datetime(2026, 10, 12, 7, 0)),
    BatchPlan(
        SYN500,
        "B26-0002",
        datetime(2026, 10, 13, 7, 0),
        defects=(
            Defect("S-DISP-02", "missing_performer"),
            Defect("S-GRAN-01", "out_of_range", side="low"),
            Defect("S-GRAN-02", "out_of_range"),
            Defect("S-GRAN-02", "missing_performer"),
            Defect("S-GRAN-03", "missing_reading"),
            Defect("S-GRAN-04", "unit_mismatch", unit="degF"),
            Defect("S-BLEND-01", "verifier_before_performer"),
            Defect("S-COMP-01", "out_of_range"),
            Defect("S-COMP-02", "missing_second_check"),
            Defect("S-COMP-03", "unit_mismatch", unit="blank"),
            Defect("S-COAT-02", "same_person"),
            Defect("S-PACK-02", "missing_step"),
        ),
        comments={
            "S-GRAN-02": INSTRUCTION_COMMENTS[0],
            "S-COMP-01": INSTRUCTION_COMMENTS[1],
        },
    ),
    BatchPlan(
        PLC250,
        "P26-0002",
        datetime(2026, 10, 13, 7, 0),
        defects=(
            Defect("S-DISP-01", "verifier_before_performer"),
            Defect("S-BLEND-01", "unit_mismatch", unit="case"),
            Defect("S-BLEND-01", "missing_performer"),
            Defect("S-BLEND-02", "out_of_range"),
            Defect("S-BLEND-03", "missing_step"),
            Defect("S-ENC-02", "same_person"),
            Defect("S-ENC-03", "out_of_range"),
            Defect("S-PACK-02", "missing_second_check"),
        ),
        comments={"S-BLEND-01": INSTRUCTION_COMMENTS[2]},
    ),
)


@dataclass(frozen=True)
class Dataset:
    """Everything a run needs: masters, executed batches, expected findings."""

    masters: list[MasterRow]
    batches: list[BatchRow]
    findings: list[ExpectedFinding]


def build_dataset(seed: int, plans: Sequence[BatchPlan] = BATCH_PLANS) -> Dataset:
    """Build the masters, the planned batches and their expected findings.

    Each batch's clean readings come from its own seed, derived from `seed`
    and its batch_id, so batches of one product differ. The planted defects
    are fixed by `plans`. The masters are those of the products the plans
    use. Expected findings are recorded while planting, never computed later.
    """
    _check_unique_batch_ids(plans)
    masters = _masters_for(plans)
    log = DefectLog()
    batches: list[BatchRow] = []
    for plan in plans:
        master = masters[_master_key(plan.product)]
        batch_seed = _batch_seed(seed, plan.batch_id)
        clean = generate_clean_batch(master, plan.batch_id, plan.start, batch_seed)
        batches += plant_defects(master, clean, plan.defects, log, plan.comments)
    master_rows = [row for rows in masters.values() for row in rows]
    return Dataset(masters=master_rows, batches=batches, findings=log.findings())


def _master_key(product: ProductConfig) -> tuple[str, str]:
    """A master is identified by its id and version."""
    return (product.master_record_id, product.master_version)


def _masters_for(plans: Sequence[BatchPlan]) -> dict[tuple[str, str], list[MasterRow]]:
    """Build each distinct product's master once, in first-use order.

    Products are the same master when their id and version match. Equal
    configs share one master; different configs under one key are refused.
    """
    products: dict[tuple[str, str], ProductConfig] = {}
    for plan in plans:
        key = _master_key(plan.product)
        if key in products and products[key] != plan.product:
            raise ValueError(
                f"different product configs claim master {key[0]} v{key[1]}"
            )
        products.setdefault(key, plan.product)
    return {key: build_master(product) for key, product in products.items()}


def _batch_seed(seed: int, batch_id: str) -> int:
    """A stable per-batch seed (crc32 is the same on every run and machine)."""
    return zlib.crc32(f"{seed}:{batch_id}".encode())


def _check_unique_batch_ids(plans: Sequence[BatchPlan]) -> None:
    """Two plans with one batch_id would merge into a single batch."""
    if duplicates := repeated(plan.batch_id for plan in plans):
        raise ValueError(f"duplicate batch_ids in plans {duplicates}")
