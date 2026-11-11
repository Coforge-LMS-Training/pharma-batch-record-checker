"""Review rules (rule_version 1.1.0), applied to matched step pairs.

Each broken rule is its own `Finding`. Rules only read a step's values and
never act on the operator comment (it is carried on the finding as data, for
the reviewer). Units are compared as exact, case-sensitive strings and never
converted. All numbers are Decimal.

Implemented so far: unit_mismatch, missing_reading, out_of_range (PHA-62),
missing_step, missing_performer (PHA-63), missing_second_check and same_person
(PHA-64, critical steps only) and verifier_before_performer (PHA-65): the 8
v1 rules. Version-2 rules 9 late_verification and 10 performed_out_of_order
are on by default (rule_version 1.1.0); RulesConfig(enable_v2_rules=False)
gives exactly the 8 v1 rules. Severity is decided later (PHA-66), not here.

Unknown or duplicate executed step IDs are never findings: matching.py
reports them as validation errors and doesn't hand those batches to the rules.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, get_args

from pharma_batch_record_checker.matching import MatchedBatch, StepPair
from pharma_batch_record_checker.records import BatchStep, MasterKey, MasterStep
from pharma_batch_record_checker.schema import TIMESTAMP_FORMAT

RULE_VERSION = "1.1.0"

# The eight v1 rule IDs, spelled as in CLAUDE.md (rule_version 1.0.0).
RuleIdV1 = Literal[
    "out_of_range",
    "missing_reading",
    "missing_step",
    "missing_performer",
    "missing_second_check",
    "same_person",
    "verifier_before_performer",
    "unit_mismatch",
]
V1_RULE_IDS: tuple[str, ...] = get_args(RuleIdV1)

# The two version-2 rules, spelled as in generator/expected_findings.py: rule 9
# late_verification and rule 10 performed_out_of_order.
V2RuleId = Literal["late_verification", "performed_out_of_order"]
V2_RULE_IDS: tuple[str, ...] = get_args(V2RuleId)

# All ten rules of rule_version 1.1.0, in the generator's order.
RuleId = Literal[RuleIdV1, V2RuleId]
RULE_IDS: tuple[str, ...] = V1_RULE_IDS + V2_RULE_IDS

# PENDING CLIENT CONFIRMATION, open question 1 (overall plan v1, section 12):
# when the unit is wrong or missing, the range check is skipped, because
# comparing a reading in another unit against the limits would mean converting
# it. If the client says both should be reported, set this to False; the
# unit_mismatch detail then stops saying the range wasn't checked.
SKIP_RANGE_ON_UNIT_MISMATCH = True


@dataclass(frozen=True)
class RulesConfig:
    """Options for the rules. The default runs all 10 rules (rule_version 1.1.0).

    enable_v2_rules=False turns off late_verification and performed_out_of_order,
    giving exactly the 8 v1 rules.
    late_verification_minutes is rule 9's threshold: a verification more than
    this many minutes after the performance is late (exactly this many passes).
    """

    late_verification_minutes: int = 60
    enable_v2_rules: bool = True

    def __post_init__(self) -> None:
        """Refuse a threshold that isn't a positive whole number of minutes."""
        minutes = self.late_verification_minutes
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise TypeError("late_verification_minutes must be a positive integer")
        if minutes < 1:
            raise ValueError("late_verification_minutes must be a positive integer")
        if not isinstance(self.enable_v2_rules, bool):
            raise TypeError("enable_v2_rules must be True or False")


DEFAULT_RULES_CONFIG = RulesConfig()


@dataclass(frozen=True, kw_only=True)
class Finding:
    """One broken rule on one step of one batch, with a snapshot of the step.

    Every finding has the same shape. The master snapshot is always filled.
    The batch snapshot is the batch row's values, all None for missing_step.
    Snapshot names are the client CSV column names; `unit` is in both CSVs,
    so it is master_unit and batch_unit here. Values keep their parsed types.
    """

    rule_id: RuleId
    batch_id: str
    step_id: str
    detail: str
    # Master snapshot.
    master_record_id: str
    master_version: str
    step_seq: int
    unit_procedure: str
    operation: str
    phase: str
    parameter: str | None
    master_unit: str | None
    min_value: Decimal | None
    max_value: Decimal | None
    critical: bool
    # Batch snapshot (all None when the step has no batch row).
    reading: Decimal | None
    batch_unit: str | None
    performed_by: str | None
    performed_at: datetime | None
    verified_by: str | None
    verified_at: datetime | None
    comment: str | None


def batch_findings(
    batch: MatchedBatch, config: RulesConfig = DEFAULT_RULES_CONFIG
) -> tuple[Finding, ...]:
    """Every finding for a matched batch, step by step in step_seq order.

    For performed_out_of_order, each step is compared with the previous master
    step whose row is present in the batch (absent steps are skipped over).
    """
    key = batch.master.key
    found: list[Finding] = []
    previous: BatchStep | None = None
    for pair in batch.pairs:
        found += step_findings(batch.batch_id, key, pair, config, previous)
        if pair.batch is not None:
            previous = pair.batch
    return tuple(found)


def step_findings(
    batch_id: str,
    master_key: MasterKey,
    pair: StepPair,
    config: RulesConfig = DEFAULT_RULES_CONFIG,
    previous: BatchStep | None = None,
) -> tuple[Finding, ...]:
    """Every implemented rule's finding for one master step of a batch.

    A step with no batch row gives only missing_step. Otherwise the order is
    fixed: unit_mismatch, missing_reading, out_of_range, missing_performer,
    missing_second_check, same_person, verifier_before_performer, then (unless
    config.enable_v2_rules is False) late_verification and performed_out_of_order.
    A sign-off-only step (no parameter) has no reading or unit to check; only
    critical steps get missing_second_check and same_person. `previous` is the
    previous present step's row (batch_findings passes it); None skips rule 10.
    """
    master, row = pair

    def finding(rule_id: RuleId, detail: str) -> Finding:
        return _finding(rule_id, batch_id, master_key, master, row, detail)

    if row is None:
        detail = f"{master.step_id} ({master.phase}) has no row in the batch record"
        return (finding("missing_step", detail),)

    found: list[Finding] = []
    if master.parameter is not None:
        unit_ok = row.unit == master.unit  # exact, case-sensitive; None never matches
        if not unit_ok:
            found.append(finding("unit_mismatch", _unit_detail(master, row.unit)))
        if row.reading is None:
            detail = f"{master.parameter}: no reading"
            found.append(finding("missing_reading", detail))
        elif unit_ok or not SKIP_RANGE_ON_UNIT_MISMATCH:
            detail = _range_detail(master, row.reading, row.unit)
            if detail is not None:
                found.append(finding("out_of_range", detail))
    if row.performed_by is None:
        found.append(finding("missing_performer", "No performer signature"))
    if master.critical:
        gap = second_check_gap(row)
        if gap is not None:
            found.append(finding("missing_second_check", gap))
        elif row.performed_by is not None and same_operator(
            row.performed_by, row.verified_by
        ):
            detail = f"{row.performed_by} both performed and verified a critical step"
            found.append(finding("same_person", detail))
    if verified_before_performed(row.performed_at, row.verified_at):
        detail = _time_order_detail(row.performed_at, row.verified_at)
        found.append(finding("verifier_before_performer", detail))
    if config.enable_v2_rules:
        limit = config.late_verification_minutes
        if verified_late(row.performed_at, row.verified_at, limit):
            detail = _late_detail(row.performed_at, row.verified_at, limit)
            found.append(finding("late_verification", detail))
        if previous is not None and performed_before_previous(
            row.performed_at, previous.performed_at
        ):
            detail = _out_of_order_detail(row.performed_at, previous)
            found.append(finding("performed_out_of_order", detail))
    return tuple(found)


def verified_late(
    performed_at: datetime | None, verified_at: datetime | None, limit_minutes: int
) -> bool:
    """Rule 9: verified more than limit_minutes after performed.

    Exactly limit_minutes passes; skipped if either time is blank.
    """
    return (
        performed_at is not None
        and verified_at is not None
        and verified_at - performed_at > timedelta(minutes=limit_minutes)
    )


def performed_before_previous(
    performed_at: datetime | None, previous_performed_at: datetime | None
) -> bool:
    """Rule 10: performed strictly before the previous present step.

    The same time passes; skipped if either time is blank.
    """
    return (
        performed_at is not None
        and previous_performed_at is not None
        and performed_at < previous_performed_at
    )


# ASSUMPTION TO CONFIRM (open question 5, overall plan v1, section 12): a
# critical step has no second check when verified_by OR verified_at is blank
# (as in the test oracle). With either one missing, same_person is not checked.
# If the client decides otherwise, change only this function.
def second_check_gap(row: BatchStep) -> str | None:
    """Why a step has no complete second check, or None if it has one."""
    if row.verified_by is None:
        return "Critical step has no second-person check"
    if row.verified_at is None:
        return f"Verifier {row.verified_by} recorded but no verification time"
    return None


# ASSUMPTION TO CONFIRM (with the client): operator IDs are compared exactly
# as written, with no trimming or case folding, so "op-104" is not "OP-104".
# If the client says otherwise, change only this function.
def same_operator(performed_by: str, verified_by: str | None) -> bool:
    """Whether the performer and verifier are the same operator ID."""
    return performed_by == verified_by


# PENDING CLIENT CONFIRMATION (overall plan v1, rule 7):
# - equal times do not flag: only a verification strictly earlier than the
#   performance is reported;
# - if either timestamp is missing, this rule is skipped.
# Timestamps are compared as parsed (naive, minute precision); no timezone is
# added or assumed. If the client decides otherwise, change only this function.
def verified_before_performed(
    performed_at: datetime | None, verified_at: datetime | None
) -> bool:
    """Whether the step was verified strictly before it was performed."""
    return (
        performed_at is not None
        and verified_at is not None
        and verified_at < performed_at
    )


def _time_order_detail(performed_at: datetime, verified_at: datetime) -> str:
    """'Verified 10:30, performed 10:45', with full timestamps across dates."""
    if verified_at.date() == performed_at.date():
        fmt = "%H:%M"
    else:
        fmt = TIMESTAMP_FORMAT
    return (
        f"Verified {verified_at.strftime(fmt)}, performed {performed_at.strftime(fmt)}"
    )


def _times(first: datetime, second: datetime) -> tuple[str, str]:
    """Both times as HH:MM on the same date, else as full timestamps."""
    fmt = "%H:%M" if first.date() == second.date() else TIMESTAMP_FORMAT
    return first.strftime(fmt), second.strftime(fmt)


def _late_detail(performed_at: datetime, verified_at: datetime, limit: int) -> str:
    """'Verified 11:35, 65 min after performed 10:30 (limit 60 min)'."""
    minutes = int((verified_at - performed_at).total_seconds() // 60)
    verified, performed = _times(verified_at, performed_at)
    return (
        f"Verified {verified}, {minutes} min after performed {performed} "
        f"(limit {limit} min)"
    )


def _out_of_order_detail(performed_at: datetime, previous: BatchStep) -> str:
    """'Performed 10:25, before S-BLEND-01 (performed 10:45)'."""
    this, before = _times(performed_at, previous.performed_at)
    return f"Performed {this}, before {previous.step_id} (performed {before})"


def _finding(
    rule_id: RuleId,
    batch_id: str,
    master_key: MasterKey,
    master: MasterStep,
    row: BatchStep | None,
    detail: str,
) -> Finding:
    """Build a finding with the master snapshot and (if present) the batch one."""
    return Finding(
        rule_id=rule_id,
        batch_id=batch_id,
        step_id=master.step_id,
        detail=detail,
        master_record_id=master_key.master_record_id,
        master_version=master_key.master_version,
        step_seq=master.step_seq,
        unit_procedure=master.unit_procedure,
        operation=master.operation,
        phase=master.phase,
        parameter=master.parameter,
        master_unit=master.unit,
        min_value=master.min_value,
        max_value=master.max_value,
        critical=master.critical,
        reading=row.reading if row else None,
        batch_unit=row.unit if row else None,
        performed_by=row.performed_by if row else None,
        performed_at=row.performed_at if row else None,
        verified_by=row.verified_by if row else None,
        verified_at=row.verified_at if row else None,
        comment=row.comment if row else None,
    )


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
