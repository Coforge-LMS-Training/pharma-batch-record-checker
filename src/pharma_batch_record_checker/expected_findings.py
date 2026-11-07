"""expected_findings.csv: the answer key for planted defects.

Expected findings are derived only from the defects the generator plants,
never from a rules engine's output, so the file can be used to check that
engine independently. Columns: batch_id, step_id, rule_id, severity.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, get_args

from pharma_batch_record_checker.schema import MasterRow, Text, _check_header, _CsvRow

EXPECTED_FINDINGS_HEADER: tuple[str, ...] = (
    "batch_id",
    "step_id",
    "rule_id",
    "severity",
)

# The eight confirmed rule_id spellings (rule_version 1.0.0).
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

# The two agreed severity values. Severity is per step: every finding on a
# step carries that step's severity.
Severity = Literal["Critical", "Needs Review"]
CRITICAL, NEEDS_REVIEW = get_args(Severity)

# On a critical step, any one of these findings makes the step Critical.
CRITICAL_ON_CRITICAL_STEP: frozenset[str] = frozenset(
    {"missing_second_check", "same_person", "out_of_range"}
)


class ExpectedFinding(_CsvRow):
    """One row of expected_findings.csv: a finding a planted defect must cause."""

    batch_id: Text
    step_id: Text
    rule_id: RuleId
    severity: Severity


_check_header(ExpectedFinding, EXPECTED_FINDINGS_HEADER)


@dataclass(frozen=True)
class PlantedDefect:
    """A defect the generator planted: one rule broken on one batch step.

    Build it with `PlantedDefect.on(...)` from the master step, so step_id
    and the critical flag are never copied by hand.
    """

    batch_id: str
    step_id: str
    critical: bool
    rule_id: str

    @classmethod
    def on(cls, batch_id: str, step: MasterRow, rule_id: str) -> "PlantedDefect":
        """Record that `rule_id` was planted on master `step` in batch `batch_id`."""
        return cls(batch_id, step.step_id, step.critical, rule_id)


def expected_findings(planted: Iterable[PlantedDefect]) -> list[ExpectedFinding]:
    """Turn planted defects into expected findings, in planted order.

    Severity is per (batch, step): Critical if the step has 2+ findings, or
    is a critical step with missing_second_check, same_person or
    out_of_range; otherwise Needs Review. Every finding on the step carries it.
    """
    planted = list(planted)
    by_step: dict[tuple[str, str], list[PlantedDefect]] = {}
    for defect in planted:
        by_step.setdefault((defect.batch_id, defect.step_id), []).append(defect)
    severity = {key: _step_severity(defects) for key, defects in by_step.items()}
    return [
        ExpectedFinding(
            batch_id=d.batch_id,
            step_id=d.step_id,
            rule_id=d.rule_id,
            severity=severity[(d.batch_id, d.step_id)],
        )
        for d in planted
    ]


def _step_severity(defects: list[PlantedDefect]) -> str:
    """The agreed per-step severity policy."""
    if len(defects) >= 2:
        return CRITICAL
    if any(d.critical and d.rule_id in CRITICAL_ON_CRITICAL_STEP for d in defects):
        return CRITICAL
    return NEEDS_REVIEW
