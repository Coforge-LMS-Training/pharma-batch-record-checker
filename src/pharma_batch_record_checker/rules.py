"""Review rules (rule_version 1.0.0), applied to matched step pairs.

Each broken rule is its own `Finding`. Rules only read a step's values and
never act on, or even read, the operator comment. Units are compared as exact,
case-sensitive strings and never converted. All numbers are Decimal.

Implemented so far (PHA-62): unit_mismatch, missing_reading, out_of_range.
Severity is decided later (PHA-66), not here.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, get_args

from pharma_batch_record_checker.matching import StepPair
from pharma_batch_record_checker.records import MasterStep

RULE_VERSION = "1.0.0"

# The eight agreed rule IDs, spelled as in CLAUDE.md (rule_version 1.0.0).
RuleId = Literal[
    "out_of_range",
    "missing_reading",
    "missing_step",
    "missing_performer",
    "missing_second_check",
    "same_person",
    "verifier_before_performer",
    "unit_mismatch",
]
RULE_IDS: tuple[str, ...] = get_args(RuleId)

# PENDING CLIENT CONFIRMATION, open question 1 (overall plan v1, section 12):
# when the unit is wrong or missing, the range check is skipped, because
# comparing a reading in another unit against the limits would mean converting
# it. If the client says both should be reported, set this to False; the
# unit_mismatch detail then stops saying the range wasn't checked.
SKIP_RANGE_ON_UNIT_MISMATCH = True


@dataclass(frozen=True)
class Finding:
    """One broken rule on one step of one batch."""

    rule_id: RuleId
    batch_id: str
    step_id: str
    detail: str


def step_findings(batch_id: str, pair: StepPair) -> tuple[Finding, ...]:
    """Every implemented rule's finding for one matched step.

    Order is fixed: unit_mismatch, missing_reading, out_of_range. A step
    absent from the batch gives nothing here (missing_step is separate). A
    sign-off-only step (no parameter) has no reading or unit to check.
    """
    master, row = pair
    if row is None or master.parameter is None:
        return ()

    def finding(rule_id: RuleId, detail: str) -> Finding:
        return Finding(rule_id, batch_id, master.step_id, detail)

    found: list[Finding] = []
    unit_ok = row.unit == master.unit  # exact and case-sensitive; None never matches
    if not unit_ok:
        found.append(finding("unit_mismatch", _unit_detail(master, row.unit)))
    if row.reading is None:
        found.append(finding("missing_reading", f"{master.parameter}: no reading"))
    elif unit_ok or not SKIP_RANGE_ON_UNIT_MISMATCH:
        detail = _range_detail(master, row.reading, row.unit)
        if detail is not None:
            found.append(finding("out_of_range", detail))
    return tuple(found)


def _unit_detail(master: MasterStep, unit: str | None) -> str:
    """'Inlet air temperature: unit 'degF', master expects 'degC'; ...'."""
    found = "unit is blank" if unit is None else f"unit {unit!r}"
    detail = f"{master.parameter}: {found}, master expects {master.unit!r}"
    if SKIP_RANGE_ON_UNIT_MISMATCH:
        detail += "; range not checked (pending client confirmation)"
    return detail


def _range_detail(master: MasterStep, reading: Decimal, unit: str | None) -> str | None:
    """Why the reading is outside its inclusive limits, or None if it isn't."""
    low, high = master.min_value, master.max_value
    if (low is None or reading >= low) and (high is None or reading <= high):
        return None
    value = f"{master.parameter} {reading} {unit or ''}".rstrip()
    if low is not None and high is not None:
        return f"{value} outside {low} to {high} (inclusive)"
    if low is not None:
        return f"{value} below minimum {low} (inclusive, no maximum)"
    return f"{value} above maximum {high} (inclusive, no minimum)"
