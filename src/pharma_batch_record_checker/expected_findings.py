"""expected_findings.csv: the answer key for planted defects.

Expected findings are derived only from the defects the generator plants,
never from a rules engine's output, so the file can be used to check that
engine independently. Columns: batch_id, step_id, rule_id, severity.
"""

from collections import Counter
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

# On a critical step, any one of these findings makes the step Critical:
# the critical-only rules plus out_of_range.
CRITICAL_ON_CRITICAL_STEP: frozenset[str] = CRITICAL_ONLY_RULES | {"out_of_range"}


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

    Build it with `PlantedDefect.on(...)` from the master step, so step_id,
    the master key, the critical flag and what the step measures are never
    copied by hand. A defect that could never fire on its step (unknown rule,
    a critical-only rule on a non-critical step, missing_reading on a step
    with no reading, out_of_range on a step with no limits) is refused here,
    at the planting call.
    """

    batch_id: str
    master_record_id: str
    master_version: str
    step_id: str
    critical: bool
    measured: bool
    has_limits: bool
    rule_id: RuleId

    @classmethod
    def on(cls, batch_id: str, step: MasterRow, rule_id: str) -> "PlantedDefect":
        """Record that `rule_id` was planted on master `step` in batch `batch_id`."""
        return cls(
            batch_id=batch_id,
            master_record_id=step.master_record_id,
            master_version=step.master_version,
            step_id=step.step_id,
            critical=step.critical,
            measured=step.parameter is not None,
            has_limits=step.min_value is not None or step.max_value is not None,
            rule_id=rule_id,
        )

    def __post_init__(self) -> None:
        """Refuse a defect whose rule can never fire on this step."""
        problem = None
        if self.rule_id not in RULE_IDS:
            problem = "unknown rule_id"
        elif self.rule_id in CRITICAL_ONLY_RULES and not self.critical:
            problem = "applies to critical steps only"
        elif self.rule_id == "missing_reading" and not self.measured:
            problem = "a sign-off-only step has no reading to be missing"
        elif self.rule_id == "out_of_range" and not self.has_limits:
            problem = "the step has no limits to be outside of"
        if problem:
            raise ValueError(
                f"batch {self.batch_id} step {self.step_id} {self.rule_id}: {problem}"
            )


def expected_findings(planted: Iterable[PlantedDefect]) -> list[ExpectedFinding]:
    """Turn planted defects into expected findings, in planted order.

    Severity is per (batch, step): Critical if the step has 2+ findings, or
    is a critical step with missing_second_check, same_person or
    out_of_range; otherwise Needs Review. Every finding on the step carries it.

    Raises ValueError if the defects planted on a step could not all fire
    (see `_check_step`), so the file never expects a finding that can't occur.
    """
    planted = list(planted)
    _check_one_master_per_batch(planted)
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


def _check_one_master_per_batch(planted: list[PlantedDefect]) -> None:
    """A batch executes one master, so all its defects share one master key.

    This also guarantees each (batch, step) group is a single master step,
    with a single critical flag.
    """
    masters: dict[str, set[str]] = {}
    for d in planted:
        masters.setdefault(d.batch_id, set()).add(
            f"{d.master_record_id} v{d.master_version}"
        )
    for batch_id, keys in masters.items():
        if len(keys) > 1:
            raise ValueError(
                f"batch {batch_id} has defects from different masters {sorted(keys)}"
            )


def _check_step(batch_id: str, step_id: str, defects: list[PlantedDefect]) -> None:
    """Refuse defects on one step that could not all produce their findings."""
    rules = [d.rule_id for d in defects]
    rule_set = set(rules)
    problems = []
    if repeated := sorted(r for r, n in Counter(rules).items() if n > 1):
        problems.append(f"rules planted more than once {repeated}")
    if "missing_step" in rule_set and len(rule_set) > 1:
        problems.append("missing_step leaves no row for other findings")
    if "out_of_range" in rule_set and rule_set & {"missing_reading", "unit_mismatch"}:
        problems.append(
            "out_of_range can't fire when the reading is missing or the unit "
            "mismatches (the range check is skipped)"
        )
    if {"missing_performer", "same_person"} <= rule_set:
        problems.append(
            "with no performer, same_person can't fire (the verifier can't "
            "equal a blank performer)"
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


def _step_severity(defects: list[PlantedDefect]) -> Severity:
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
