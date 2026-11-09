"""Values the review templates render.

Readings stay in the notation the store returned. A blank reading stays
blank, and it is never shown as zero. Times the store created are shown
in UTC, which is the day the list filters on.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from pharma_batch_record_checker.store.reads import StoredFinding, StoredNote

NEEDS_A_LOOK = frozenset({"Critical", "Needs Review"})


def format_store_time(value: datetime) -> str:
    """A store timestamp as minute-precision UTC."""
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def format_recorded_time(value: datetime | None) -> str:
    """A CSV timestamp as stored, or the word blank."""
    if value is None:
        return "blank"
    return value.strftime("%Y-%m-%dT%H:%M")


def format_decimal(value: Decimal | None) -> str:
    """A stored decimal with its scale, or the word blank."""
    if value is None:
        return "blank"
    return format(value, "f")


def _reading_with_unit(reading: Decimal | None, unit: str | None) -> str:
    """The stored reading and the unit recorded beside it."""
    if reading is None:
        return "blank"
    if unit is None or unit == "":
        return format(reading, "f")
    return f"{format(reading, 'f')} {unit}"


def format_text(value: str | None) -> str:
    """Stored text, or the word blank when the column is null."""
    if value is None or value == "":
        return "blank"
    return value


def format_limits(
    min_value: Decimal | None,
    max_value: Decimal | None,
    unit: str | None,
) -> str:
    """The master bounds. A null bound is no limit on that side."""
    unit_text = "" if unit is None else f" {unit}"
    if min_value is None and max_value is None:
        return "no limit"
    if min_value is None:
        return f"at most {format(max_value, 'f')}{unit_text}"
    if max_value is None:
        return f"at least {format(min_value, 'f')}{unit_text}"
    return f"{format(min_value, 'f')} to {format(max_value, 'f')}{unit_text}"


class NoteView:
    """One note, oldest first under its finding."""

    def __init__(self, reviewer_name: str, note_text: str, noted_at: str) -> None:
        self.reviewer_name = reviewer_name
        self.note_text = note_text
        self.noted_at = noted_at


class FindingView:
    """One broken rule and the snapshot stored with it."""

    def __init__(self, finding: StoredFinding, notes: Sequence[StoredNote]) -> None:
        self.finding_id = finding.finding_id
        self.rule_id = finding.rule_id
        self.step_severity = finding.step_severity
        self.detail = finding.detail
        self.reading = _reading_with_unit(finding.reading, finding.recorded_unit)
        self.recorded_unit = format_text(finding.recorded_unit)
        self.expected_unit = format_text(finding.expected_unit)
        self.limits = format_limits(
            finding.min_value, finding.max_value, finding.expected_unit
        )
        self.performed_by = format_text(finding.performed_by)
        self.performed_at = format_recorded_time(finding.performed_at)
        self.verified_by = format_text(finding.verified_by)
        self.verified_at = format_recorded_time(finding.verified_at)
        self.master_critical = "yes" if finding.critical else "no"
        self.comment = finding.comment
        self.notes = tuple(
            NoteView(
                note.reviewer_name,
                note.note_text,
                format_store_time(note.noted_at),
            )
            for note in notes
        )


class StepView:
    """One step and every finding on it, in finding order."""

    def __init__(self, step_id: str, step_seq: int, parameter: str) -> None:
        self.step_id = step_id
        self.step_seq = step_seq
        self.parameter = parameter
        self.findings: list[FindingView] = []


class PhaseView:
    """One phase and its steps."""

    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.steps: list[StepView] = []


class OperationView:
    """One operation and its phases."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        self.phases: list[PhaseView] = []


class ProcedureView:
    """One unit procedure and its operations."""

    def __init__(self, unit_procedure: str) -> None:
        self.unit_procedure = unit_procedure
        self.operations: list[OperationView] = []


def group_findings(
    findings: Sequence[StoredFinding],
    notes_by_finding: dict[str, Sequence[StoredNote]],
) -> tuple[ProcedureView, ...]:
    """Group findings in list order by procedure, operation, phase, and step.

    Two findings on one step stay two findings. The first time a group is
    seen fixes its place, which follows finding_seq.
    """
    procedures: dict[str, ProcedureView] = {}
    operations: dict[tuple[str, str], OperationView] = {}
    phases: dict[tuple[str, str, str], PhaseView] = {}
    steps: dict[tuple[str, str, str, str], StepView] = {}
    for finding in findings:
        procedure = procedures.get(finding.unit_procedure)
        if procedure is None:
            procedure = ProcedureView(finding.unit_procedure)
            procedures[finding.unit_procedure] = procedure
        operation_key = (finding.unit_procedure, finding.operation)
        operation = operations.get(operation_key)
        if operation is None:
            operation = OperationView(finding.operation)
            operations[operation_key] = operation
            procedure.operations.append(operation)
        phase_key = (finding.unit_procedure, finding.operation, finding.phase)
        phase = phases.get(phase_key)
        if phase is None:
            phase = PhaseView(finding.phase)
            phases[phase_key] = phase
            operation.phases.append(phase)
        step_key = (
            finding.unit_procedure,
            finding.operation,
            finding.phase,
            finding.step_id,
        )
        step = steps.get(step_key)
        if step is None:
            step = StepView(
                finding.step_id,
                finding.step_seq,
                format_text(finding.parameter),
            )
            steps[step_key] = step
            phase.steps.append(step)
        step.findings.append(
            FindingView(finding, notes_by_finding.get(finding.finding_id, ()))
        )
    return tuple(procedures.values())
