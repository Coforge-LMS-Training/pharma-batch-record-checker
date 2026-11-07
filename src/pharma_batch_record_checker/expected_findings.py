"""expected_findings.csv: the answer key for planted defects.

Expected findings are derived only from the defects the generator plants,
never from a rules engine's output, so the file can be used to check that
engine independently. Columns: batch_id, step_id, rule_id, severity.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, get_args

from pharma_batch_record_checker.csv_io import Destination, _write_csv
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

# Rules that apply to critical steps only.
CRITICAL_ONLY_RULES: frozenset[str] = frozenset({"missing_second_check", "same_person"})

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

    Raises ValueError if the defects planted on a step could not all fire
    (see `_check_step`), so the file never expects a finding that can't occur.
    """
    planted = list(planted)
    by_step: dict[tuple[str, str], list[PlantedDefect]] = {}
    for defect in planted:
        by_step.setdefault((defect.batch_id, defect.step_id), []).append(defect)
    for (batch_id, step_id), defects in by_step.items():
        _check_step(batch_id, step_id, defects)
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


def _check_step(batch_id: str, step_id: str, defects: list[PlantedDefect]) -> None:
    """Refuse defects on one step that could not all produce their findings."""
    rules = [d.rule_id for d in defects]
    rule_set = set(rules)
    problems = []
    if unknown := sorted(rule_set - set(RULE_IDS)):
        problems.append(f"unknown rule_ids {unknown}")
    if repeated := sorted({r for r in rules if rules.count(r) > 1}):
        problems.append(f"rules planted more than once {repeated}")
    if not defects[0].critical and (only := sorted(rule_set & CRITICAL_ONLY_RULES)):
        problems.append(f"{only} apply to critical steps only")
    if "missing_step" in rule_set and len(rule_set) > 1:
        problems.append("missing_step leaves no row for other findings")
    if "out_of_range" in rule_set and rule_set & {"missing_reading", "unit_mismatch"}:
        problems.append(
            "out_of_range can't fire when the reading is missing or the unit "
            "mismatches (the range check is skipped)"
        )
    if "missing_second_check" in rule_set and rule_set & {
        "same_person",
        "verifier_before_performer",
    }:
        problems.append(
            "missing_second_check leaves no verifier for same_person or "
            "verifier_before_performer"
        )
    if problems:
        raise ValueError(
            f"batch {batch_id} step {step_id} {sorted(rule_set)}: "
            + "; ".join(problems)
        )


def _step_severity(defects: list[PlantedDefect]) -> str:
    """The agreed per-step severity policy."""
    if len(defects) >= 2:
        return CRITICAL
    if any(d.critical and d.rule_id in CRITICAL_ON_CRITICAL_STEP for d in defects):
        return CRITICAL
    return NEEDS_REVIEW


def write_expected_findings(
    findings: Iterable[ExpectedFinding], dest: Destination
) -> None:
    """Write expected_findings.csv: exact header, CRLF, atomic (as other CSVs)."""
    _write_csv(EXPECTED_FINDINGS_HEADER, findings, dest)


class DefectLog:
    """Records defects as they are planted, then produces the expected findings.

    A planter changes a clean batch row and immediately calls
    `record(batch_id, step, rule_id)` with the master step it changed.
    """

    def __init__(self) -> None:
        """Start with nothing planted."""
        self._planted: list[PlantedDefect] = []

    def record(self, batch_id: str, step: MasterRow, rule_id: str) -> None:
        """Record that `rule_id` was planted on master `step` in `batch_id`."""
        self._planted.append(PlantedDefect.on(batch_id, step, rule_id))

    @property
    def planted(self) -> list[PlantedDefect]:
        """Everything recorded so far, in planting order (a copy)."""
        return list(self._planted)

    def findings(self) -> list[ExpectedFinding]:
        """The expected findings for everything planted (see expected_findings)."""
        return expected_findings(self._planted)

    def write(self, dest: Destination) -> None:
        """Write the expected findings to `dest` in expected_findings.csv format."""
        write_expected_findings(self.findings(), dest)
