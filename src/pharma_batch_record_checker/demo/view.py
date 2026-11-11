"""Values the review templates render.

Readings stay in the notation the store returned. A blank value is never
shown as zero: it is "Missing" when the finding is about that field, and
"Not recorded" otherwise. Times the store created are shown in UTC, which is
the day the list filters on.

Rule ids stay as stored. Each one is also given a plain-language title so a
reader who does not know the codes can still follow the page; the code is
shown beside the title, never replaced by it.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from pharma_batch_record_checker.store.reads import (
    StoredFinding,
    StoredMasterStep,
    StoredNote,
)

NEEDS_A_LOOK = frozenset({"Critical", "Needs Review"})

# Status slugs used in URLs (?status=...) and CSS classes, per result word.
STATUS_SLUGS: dict[str, str] = {
    "Critical": "critical",
    "Needs Review": "needs-review",
    "No Findings": "no-findings",
}

MISSING = "Missing"
NOT_RECORDED = "Not recorded"


@dataclass(frozen=True)
class RuleGuide:
    """How the page names one rule, and which snapshot fields it is about."""

    title: str
    meaning: str
    fields: frozenset[str]


# One entry per rule id in rule_version 1.1.0. The field names are
# FindingView attributes; they mark the values a reader should look at first.
RULE_GUIDE: dict[str, RuleGuide] = {
    "out_of_range": RuleGuide(
        "Reading outside the allowed range",
        "The recorded value is below the minimum or above the maximum in the "
        "master record. Limits are inclusive.",
        frozenset({"reading", "limits"}),
    ),
    "missing_reading": RuleGuide(
        "No reading recorded",
        "The step asks for a value, but the batch record has none. A blank "
        "reading is not treated as zero.",
        frozenset({"reading"}),
    ),
    "missing_step": RuleGuide(
        "Step not recorded",
        "The master record has this step, but the batch record has no row for it.",
        frozenset(),
    ),
    "missing_performer": RuleGuide(
        "No performer signature",
        "Nobody is recorded as having performed the step.",
        frozenset({"performed_by"}),
    ),
    "missing_second_check": RuleGuide(
        "No second-person check",
        "A critical step needs a verifier and a verification time. One or both "
        "are missing.",
        frozenset({"verified_by", "verified_at"}),
    ),
    "same_person": RuleGuide(
        "Checked by the person who did it",
        "On a critical step, the verifier must be a different person from the "
        "performer.",
        frozenset({"performed_by", "verified_by"}),
    ),
    "verifier_before_performer": RuleGuide(
        "Checked before it was done",
        "The verification time is earlier than the time the step was performed.",
        frozenset({"performed_at", "verified_at"}),
    ),
    "unit_mismatch": RuleGuide(
        "Unit differs from the master",
        "The recorded unit is not exactly the master's unit. Units are never "
        "converted, so the range is not checked.",
        frozenset({"recorded_unit", "expected_unit"}),
    ),
    "late_verification": RuleGuide(
        "Checked long after it was done",
        "The verification was recorded more than 60 minutes after the step was "
        "performed. Exactly 60 minutes is fine.",
        frozenset({"performed_at", "verified_at"}),
    ),
    "performed_out_of_order": RuleGuide(
        "Done before the step before it",
        "The step was performed earlier than the previous step in the master "
        "record that the batch has. The same time is fine.",
        frozenset({"performed_at"}),
    ),
}

# What each result word means, in the words the page uses.
RESULT_GUIDE: tuple[tuple[str, str], ...] = (
    (
        "Critical",
        (
            "A critical step is out of range, has no second check, or was "
            "checked by the person who did it; or one step has two or more "
            "findings."
        ),
    ),
    ("Needs Review", "A step has one finding that a person should look at."),
    ("No Findings", "Every step matches its master record."),
)


@dataclass(frozen=True)
class Term:
    """One glossary entry, written for a reader with no pharma background."""

    key: str
    term: str
    meaning: str


GLOSSARY: tuple[Term, ...] = (
    Term(
        "allowed-range",
        "Allowed range (limits)",
        "The lowest and highest value a reading may have, set by the master "
        "record. Both ends count as inside the range.",
    ),
    Term(
        "api",
        "API (active pharmaceutical ingredient)",
        "The substance in a medicine that has the effect. Here it is weighed "
        "in the first dispensing step.",
    ),
    Term(
        "batch",
        "Batch",
        "One production run of a product, made to the master record. Each batch "
        "has an ID such as B26-0413.",
    ),
    Term(
        "batch-record",
        "Batch record (executed record)",
        "What operators actually recorded while making one batch: readings, "
        "units, who did each step and when, and who checked it.",
    ),
    Term(
        "comment",
        "Operator comment",
        "Free text an operator wrote on a step. It is shown exactly as written "
        "and never changes a result, even if it reads like an instruction.",
    ),
    Term(
        "critical-step",
        "Critical step",
        "A step the master record marks as important to quality. It must be "
        "checked by a second, different person.",
    ),
    Term(
        "csv",
        "CSV export",
        "A spreadsheet file with a batch's full review history: every run, "
        "finding and note. It is what an auditor would read.",
    ),
    Term(
        "excipient",
        "Excipient",
        "An inactive ingredient, such as a filler or binder, that gives the "
        "tablet or capsule its form.",
    ),
    Term(
        "finding",
        "Finding",
        "One check that a step did not pass. A step that breaks two checks has "
        "two findings.",
    ),
    Term(
        "master-record",
        "Master record (master batch record)",
        "The approved recipe for a product: every step in order, what to "
        "measure, the unit, the allowed range, and which steps are critical. "
        "Each version, such as MBR-SYN500 v3, is fixed once approved.",
    ),
    Term(
        "missing",
        "Missing / Not recorded",
        "The batch record has no value in that field. 'Missing' marks the field "
        "a finding is about; 'Not recorded' is a blank the checks did not need.",
    ),
    Term(
        "note",
        "Note",
        "A comment a reviewer adds to a finding. Notes are kept forever, with "
        "the name and time, and never change the finding.",
    ),
    Term(
        "performer",
        "Performer",
        "The operator who carried out a step, recorded by an operator ID such "
        "as OP-108.",
    ),
    Term(
        "phase",
        "Unit procedure, operation and phase",
        "The three levels a process is split into: a stage such as Granulation, "
        "a task within it such as Wet granulation, and a single action such as "
        "Granulate.",
    ),
    Term(
        "process",
        "Dispensing, granulation, blending, compression, coating, packaging",
        "The usual stages of making tablets: weighing ingredients, forming "
        "granules, mixing, pressing tablets, applying a film coat, and packing.",
    ),
    Term(
        "reading",
        "Reading and unit",
        "The value an operator recorded for a step, and the unit it was "
        "recorded in. Units must match the master exactly; they are never "
        "converted.",
    ),
    Term(
        "result",
        "Result",
        "The overall outcome of a review: Critical, Needs Review, or No "
        "Findings. A batch takes the most serious result of its steps.",
    ),
    Term(
        "run",
        "Review run",
        "One automated check of a batch. Each run is stored separately and "
        "never overwritten, so the history shows every review.",
    ),
    Term(
        "rule-version",
        "Rule version",
        "Which version of the ten checks produced a run, currently 1.1.0, so "
        "an old result can always be explained.",
    ),
    Term(
        "step",
        "Step (step ID)",
        "One numbered instruction in the master record, such as S-DISP-01 "
        "(dispensing, step 1).",
    ),
    Term(
        "synthetic",
        "Synthetic data",
        "Made-up records for demonstration. No real product, batch or person "
        "appears anywhere on this site.",
    ),
    Term(
        "utc",
        "UTC",
        "Coordinated Universal Time, the world reference clock. Review times "
        "and the date filter use it so everyone sees the same day.",
    ),
    Term(
        "verifier",
        "Verifier (second-person check)",
        "A second operator who checks a step after it is done and records "
        "their ID and the time.",
    ),
)

GLOSSARY_BY_KEY: dict[str, Term] = {term.key: term for term in GLOSSARY}

FAQ: tuple[tuple[str, str], ...] = (
    (
        "What does this site do?",
        (
            "It compares each batch record with its master record using ten "
            "checks, and lists every place a person should look. It does not "
            "decide anything on its own."
        ),
    ),
    (
        "Can it approve, release or reject a batch?",
        (
            "No. It only flags and reports. Records cannot be edited here, and "
            "there is no approve, release or reject button anywhere."
        ),
    ),
    (
        "Why does a field say Missing?",
        (
            "The batch record has nothing in that field, and that empty field is "
            "the reason for the finding. For example, a critical step with no "
            "verifier shows Verified by: Missing."
        ),
    ),
    (
        "Why does one step have two findings?",
        (
            "Each broken check is its own finding. A step with two or more "
            "findings is always Critical."
        ),
    ),
    (
        "Why is the range not checked when the unit is different?",
        (
            "Comparing a value in another unit would mean converting it, and the "
            "checks never convert units. The unit mismatch is reported instead."
        ),
    ),
    (
        "Can I change or delete a note?",
        (
            "No. Notes are append-only, like the rest of the history. Add another "
            "note to correct or extend one."
        ),
    ),
    (
        "What is in the CSV download?",
        (
            "Every review run of the batch, every finding with the values it was "
            "judged on, and every note. The email button sends a link to that file."
        ),
    ),
    (
        "Is this real data?",
        ("No. Every product, batch and operator is synthetic."),
    ),
)

# Which glossary entries each page explains at its foot.
PAGE_TERMS: dict[str, tuple[str, ...]] = {
    "overview": ("batch", "master-record", "batch-record", "finding", "result"),
    "batches": ("batch", "master-record", "result", "finding", "run", "utc"),
    "batch": (
        "step",
        "critical-step",
        "reading",
        "allowed-range",
        "performer",
        "verifier",
        "missing",
        "comment",
        "note",
        "run",
    ),
}


def page_terms(page: str) -> tuple[Term, ...]:
    """The glossary entries shown at the foot of one page."""
    return tuple(GLOSSARY_BY_KEY[key] for key in PAGE_TERMS.get(page, ()))


def rule_guide(rule_id: str) -> RuleGuide:
    """The guide for a rule id. An unknown id is shown as its own code."""
    return RULE_GUIDE.get(rule_id, RuleGuide(rule_id, "", frozenset()))


def status_slug(category: str | None) -> str:
    """'critical', 'needs-review', 'no-findings', or 'none' with no review."""
    if not category:
        return "none"
    return STATUS_SLUGS.get(category, category.replace(" ", "-").lower())


def result_class(category: str | None) -> str:
    """CSS classes for a result badge, e.g. 'badge badge-critical'."""
    return f"badge badge-{status_slug(category)}"


def format_store_time(value: datetime) -> str:
    """A store timestamp as minute-precision UTC."""
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def format_day(value: datetime) -> str:
    """A store timestamp as its UTC day, e.g. 'Tue 10 Nov 2026'."""
    return value.astimezone(UTC).strftime("%a %-d %b %Y")


def format_recorded_time(value: datetime | None) -> str | None:
    """A CSV timestamp as stored, or None when it is blank."""
    if value is None:
        return None
    return value.strftime("%Y-%m-%dT%H:%M")


def format_decimal(value: Decimal | None) -> str:
    """A stored decimal with its scale, or the word blank."""
    if value is None:
        return "blank"
    return format(value, "f")


def _reading_with_unit(reading: Decimal | None, unit: str | None) -> str | None:
    """The stored reading and the unit recorded beside it, or None."""
    if reading is None:
        return None
    if unit is None or unit == "":
        return format(reading, "f")
    return f"{format(reading, 'f')} {unit}"


def format_text(value: str | None) -> str | None:
    """Stored text, or None when the column is null or empty."""
    if value is None or value == "":
        return None
    return value


def format_limits(
    min_value: Decimal | None,
    max_value: Decimal | None,
    unit: str | None,
) -> str:
    """The master bounds. A null bound is no limit on that side."""
    unit_text = "" if unit is None else f" {unit}"
    if min_value is None and max_value is None:
        return "No limit"
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


@dataclass(frozen=True)
class SnapshotField:
    """One label and value in a finding's snapshot.

    flagged: the finding is about this field. missing: the stored value is
    blank. A flagged blank is shown as "Missing"; any other blank as
    "Not recorded".
    """

    name: str
    label: str
    value: str
    flagged: bool
    missing: bool


@dataclass(frozen=True)
class RangeView:
    """Where a reading sits against its limits, as percentages of a track."""

    low: str
    high: str
    reading: str
    band_left: float
    band_width: float
    marker: float
    inside: bool


def range_view(
    reading: Decimal | None,
    min_value: Decimal | None,
    max_value: Decimal | None,
    units_match: bool,
) -> RangeView | None:
    """A track with the allowed band in the middle half and the reading on it.

    Only drawn when both limits and the reading exist and the units match;
    a reading in another unit is never placed on the master's scale.
    """
    if reading is None or min_value is None or max_value is None or not units_match:
        return None
    span = max_value - min_value
    if span <= 0:
        return None
    lo = min_value - span / 2
    hi = max_value + span / 2

    def pct(value: Decimal) -> float:
        clamped = min(max(value, lo), hi)
        return round(float((clamped - lo) / (hi - lo) * 100), 2)

    return RangeView(
        low=format(min_value, "f"),
        high=format(max_value, "f"),
        reading=format(reading, "f"),
        band_left=pct(min_value),
        band_width=round(pct(max_value) - pct(min_value), 2),
        marker=pct(reading),
        inside=min_value <= reading <= max_value,
    )


# Snapshot order and labels. The attribute names match RuleGuide.fields.
_SNAPSHOT: tuple[tuple[str, str], ...] = (
    ("reading", "Reading"),
    ("limits", "Allowed range"),
    ("recorded_unit", "Recorded unit"),
    ("expected_unit", "Expected unit"),
    ("performed_by", "Performed by"),
    ("performed_at", "Performed at"),
    ("verified_by", "Verified by"),
    ("verified_at", "Verified at"),
    ("master_critical", "Critical step"),
)


class FindingView:
    """One broken rule and the snapshot stored with it."""

    def __init__(self, finding: StoredFinding, notes: Sequence[StoredNote]) -> None:
        guide = rule_guide(finding.rule_id)
        self.finding_id = finding.finding_id
        self.rule_id = finding.rule_id
        self.title = guide.title
        self.meaning = guide.meaning
        self.step_severity = finding.step_severity
        self.status = status_slug(finding.step_severity)
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
        self.master_critical = "Yes" if finding.critical else "No"
        self.comment = finding.comment
        self.is_missing_step = finding.rule_id == "missing_step"
        self.snapshot = tuple(
            self._field(name, label, name in guide.fields) for name, label in _SNAPSHOT
        )
        self.missing_labels = tuple(
            f.label for f in self.snapshot if f.flagged and f.missing
        )
        # The range picture only where the range is the point of the finding.
        self.range = (
            range_view(
                finding.reading,
                finding.min_value,
                finding.max_value,
                finding.recorded_unit == finding.expected_unit,
            )
            if finding.rule_id == "out_of_range"
            else None
        )
        self.notes = tuple(
            NoteView(
                note.reviewer_name,
                note.note_text,
                format_store_time(note.noted_at),
            )
            for note in notes
        )

    def _field(self, name: str, label: str, flagged: bool) -> SnapshotField:
        value = getattr(self, name)
        missing = value is None
        if missing:
            shown = MISSING if flagged else NOT_RECORDED
        else:
            shown = value
        return SnapshotField(name, label, shown, flagged, missing)


class StepView:
    """One step, where it sits in the process, and every finding on it."""

    def __init__(
        self,
        step_id: str,
        step_seq: int,
        parameter: str | None,
        unit_procedure: str,
        operation: str,
        phase: str,
    ) -> None:
        self.step_id = step_id
        self.step_seq = step_seq
        self.parameter = parameter or "Sign-off"
        self.unit_procedure = unit_procedure
        self.operation = operation
        self.phase = phase
        self.findings: list[FindingView] = []

    @property
    def severity(self) -> str:
        """The step's severity. Every finding on a step carries the same one."""
        return self.findings[0].step_severity

    @property
    def status(self) -> str:
        return status_slug(self.severity)


class ProcedureView:
    """One unit procedure and its flagged steps, in process order."""

    def __init__(self, unit_procedure: str) -> None:
        self.unit_procedure = unit_procedure
        self.steps: list[StepView] = []


def group_findings(
    findings: Sequence[StoredFinding],
    notes_by_finding: dict[str, Sequence[StoredNote]],
) -> tuple[ProcedureView, ...]:
    """Group findings in list order by procedure, then step.

    Each step keeps its operation and phase, so the page can say where it
    sits without a heading per level. Two findings on one step stay two
    findings. The first time a group is seen fixes its place, which follows
    finding_seq.
    """
    procedures: dict[str, ProcedureView] = {}
    steps: dict[tuple[str, str], StepView] = {}
    for finding in findings:
        procedure = procedures.get(finding.unit_procedure)
        if procedure is None:
            procedure = ProcedureView(finding.unit_procedure)
            procedures[finding.unit_procedure] = procedure
        step_key = (finding.unit_procedure, finding.step_id)
        step = steps.get(step_key)
        if step is None:
            step = StepView(
                finding.step_id,
                finding.step_seq,
                format_text(finding.parameter),
                finding.unit_procedure,
                finding.operation,
                finding.phase,
            )
            steps[step_key] = step
            procedure.steps.append(step)
        step.findings.append(
            FindingView(finding, notes_by_finding.get(finding.finding_id, ()))
        )
    return tuple(procedures.values())


@dataclass(frozen=True)
class Overview:
    """Counts and one row per finding for the top of a batch page."""

    findings: int
    steps: int
    critical_steps: int
    review_steps: int
    critical_findings: int
    review_findings: int
    rows: tuple[tuple[StepView, FindingView], ...]

    @property
    def sentence(self) -> str:
        """'10 findings on 9 steps. By step: 3 Critical, 6 Needs Review.'"""
        if not self.findings:
            return "No findings. Every step matches its master record."
        parts = []
        if self.critical_steps:
            parts.append(f"{self.critical_steps} Critical")
        if self.review_steps:
            parts.append(f"{self.review_steps} Needs Review")
        return (
            f"{_count(self.findings, 'finding')} on {_count(self.steps, 'step')}. "
            f"By step: {', '.join(parts)}."
        )


def overview(groups: Sequence[ProcedureView]) -> Overview:
    """Summarise grouped findings: totals, steps by severity, and the rows."""
    steps = [step for procedure in groups for step in procedure.steps]
    rows = tuple((step, finding) for step in steps for finding in step.findings)
    return Overview(
        findings=len(rows),
        steps=len(steps),
        critical_steps=sum(step.severity == "Critical" for step in steps),
        review_steps=sum(step.severity == "Needs Review" for step in steps),
        critical_findings=sum(f.step_severity == "Critical" for _, f in rows),
        review_findings=sum(f.step_severity == "Needs Review" for _, f in rows),
        rows=rows,
    )


@dataclass(frozen=True)
class StripStep:
    """One master step on the process strip: flagged or clean."""

    step_seq: int
    step_id: str
    label: str
    unit_procedure: str
    status: str
    anchor: str | None
    findings: tuple[str, ...] = field(default=())


def process_strip(
    master_steps: Sequence[StoredMasterStep],
    groups: Sequence[ProcedureView],
) -> tuple[StripStep, ...]:
    """Every master step in order, marked with its result in this run."""
    flagged = {step.step_id: step for g in groups for step in g.steps}
    strip = []
    for master in master_steps:
        step = flagged.get(master.step_id)
        strip.append(
            StripStep(
                step_seq=master.step_seq,
                step_id=master.step_id,
                label=master.parameter or master.phase,
                unit_procedure=master.unit_procedure,
                status="clean" if step is None else step.status,
                anchor=None if step is None else f"step-{step.step_id}",
                findings=() if step is None else tuple(f.title for f in step.findings),
            )
        )
    return tuple(strip)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"
