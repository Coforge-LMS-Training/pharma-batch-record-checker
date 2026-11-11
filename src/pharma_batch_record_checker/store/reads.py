"""Select stored masters, batches, runs, findings, notes, and one CSV.

These functions do not insert, and they do not call the rules engine.
export_batch_csv is built from the reads below. One row per finding. A run
with no findings still produces one row, with the finding columns empty.
Notes on a finding are that finding's note text joined with a newline, in
noted_at order. The file is UTF-8 with CRLF line endings.

list_batches carries each batch's latest result_category and created_at.
list_runs is oldest first. list_runs_on keeps the runs whose created_at
falls on the given calendar day in UTC. list_findings follows finding_seq.
list_notes is oldest first.
"""

import csv
import io
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from decimal import Decimal

from psycopg import Connection
from psycopg.rows import dict_row

from pharma_batch_record_checker.handoff import PAYLOAD_FIELDS
from pharma_batch_record_checker.schema import TIMESTAMP_FORMAT
from pharma_batch_record_checker.store.connection import transaction
from pharma_batch_record_checker.store.errors import BatchNotFound, MasterNotFound

EXPORT_COLUMNS: tuple[str, ...] = (
    "batch_id",
    "run_id",
    "reviewed_at",
    "rule_version",
    "result_category",
    "step_seq",
    "step_id",
    "unit_procedure",
    "operation",
    "phase",
    "rule_id",
    "step_severity",
    "detail",
    "expected_unit",
    "recorded_unit",
    "reading",
    "min_value",
    "max_value",
    "performed_by",
    "performed_at",
    "verified_by",
    "verified_at",
    "comment",
    "notes",
)


@dataclass(frozen=True)
class StoredMasterStep:
    """One master step, in step_seq order under its version."""

    step_seq: int
    step_id: str
    unit_procedure: str
    operation: str
    phase: str
    parameter: str | None
    unit: str | None
    min_value: Decimal | None
    max_value: Decimal | None
    critical: bool


@dataclass(frozen=True)
class StoredMaster:
    """One master version and its steps."""

    master_record_id: str
    master_version: str
    product_name: str
    batch_size: Decimal
    batch_size_unit: str
    steps: tuple[StoredMasterStep, ...]


@dataclass(frozen=True)
class StoredBatchStep:
    """One executed step. Order follows the master step_seq."""

    step_id: str
    reading: Decimal | None
    unit: str | None
    performed_by: str | None
    performed_at: datetime | None
    verified_by: str | None
    verified_at: datetime | None
    comment: str | None


@dataclass(frozen=True)
class StoredBatch:
    """One executed batch and its steps."""

    batch_id: str
    master_record_id: str
    master_version: str
    steps: tuple[StoredBatchStep, ...]


@dataclass(frozen=True)
class BatchSummary:
    """One stored batch and the latest run's result, when a run exists."""

    batch_id: str
    master_record_id: str
    master_version: str
    result_category: str | None
    created_at: datetime | None


@dataclass(frozen=True)
class StoredRun:
    """One stored review run."""

    run_id: str
    batch_id: str
    rule_version: str
    result_category: str
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class StoredFinding:
    """One stored finding: its ids plus the 23 payload fields."""

    finding_id: str
    run_id: str
    finding_seq: int
    rule_id: str
    step_severity: str
    detail: str
    batch_id: str
    master_record_id: str
    master_version: str
    step_id: str
    step_seq: int
    unit_procedure: str
    operation: str
    phase: str
    parameter: str | None
    critical: bool
    expected_unit: str | None
    min_value: Decimal | None
    max_value: Decimal | None
    reading: Decimal | None
    recorded_unit: str | None
    performed_by: str | None
    performed_at: datetime | None
    verified_by: str | None
    verified_at: datetime | None
    comment: str | None


@dataclass(frozen=True)
class StoredNote:
    """One reviewer note. noted_at is the time the database stored."""

    note_id: str
    finding_id: str
    reviewer_name: str
    note_text: str
    noted_at: datetime


if tuple(field.name for field in fields(StoredFinding)) != (
    "finding_id",
    "run_id",
    "finding_seq",
    *PAYLOAD_FIELDS,
):
    raise RuntimeError("StoredFinding fields drifted from the finding payload")


def get_master(master_record_id: str, master_version: str) -> StoredMaster:
    """One master version and its steps in step_seq order."""
    with _query() as conn:
        header = conn.execute(
            """
            SELECT product_name, batch_size, batch_size_unit
            FROM master_record
            WHERE master_record_id = %s AND master_version = %s
            """,
            (master_record_id, master_version),
        ).fetchone()
        if header is None:
            raise MasterNotFound(f"no master {master_record_id} v{master_version}")
        steps = conn.execute(
            """
            SELECT step_seq, step_id, unit_procedure, operation, phase,
                   parameter, unit, min_value, max_value, critical
            FROM master_step
            WHERE master_record_id = %s AND master_version = %s
            ORDER BY step_seq
            """,
            (master_record_id, master_version),
        ).fetchall()
    return StoredMaster(
        master_record_id=master_record_id,
        master_version=master_version,
        product_name=header["product_name"],
        batch_size=header["batch_size"],
        batch_size_unit=header["batch_size_unit"],
        steps=tuple(_master_step(row) for row in steps),
    )


def get_batch(batch_id: str) -> StoredBatch:
    """One batch and its executed steps, in master step_seq order."""
    with _query() as conn:
        header = conn.execute(
            """
            SELECT master_record_id, master_version
            FROM batch
            WHERE batch_id = %s
            """,
            (batch_id,),
        ).fetchone()
        if header is None:
            raise BatchNotFound(f"no batch {batch_id}")
        steps = conn.execute(
            """
            SELECT batch_step.step_id, batch_step.reading, batch_step.unit,
                   batch_step.performed_by, batch_step.performed_at,
                   batch_step.verified_by, batch_step.verified_at,
                   batch_step.comment
            FROM batch_step
            JOIN batch ON batch.batch_id = batch_step.batch_id
            LEFT JOIN master_step
                ON master_step.master_record_id = batch.master_record_id
               AND master_step.master_version = batch.master_version
               AND master_step.step_id = batch_step.step_id
            WHERE batch_step.batch_id = %s
            ORDER BY master_step.step_seq NULLS LAST,
                     batch_step.step_id COLLATE "C"
            """,
            (batch_id,),
        ).fetchall()
    return StoredBatch(
        batch_id=batch_id,
        master_record_id=header["master_record_id"],
        master_version=header["master_version"],
        steps=tuple(_batch_step(row) for row in steps),
    )


def list_batches() -> tuple[BatchSummary, ...]:
    """Every stored batch and its latest run, in batch_id order.

    A batch with no run has a null result_category and created_at. Latest
    means the greatest created_at. When two runs share that time, the
    greater run_id is the one returned.
    """
    with _query() as conn:
        rows = conn.execute(
            """
            SELECT batch.batch_id, batch.master_record_id, batch.master_version,
                   latest.result_category, latest.created_at
            FROM batch
            LEFT JOIN LATERAL (
                SELECT result_category, created_at
                FROM review_run
                WHERE review_run.batch_id = batch.batch_id
                ORDER BY created_at DESC, run_id DESC
                LIMIT 1
            ) AS latest ON TRUE
            ORDER BY batch.batch_id COLLATE "C"
            """
        ).fetchall()
    return tuple(
        BatchSummary(
            batch_id=row["batch_id"],
            master_record_id=row["master_record_id"],
            master_version=row["master_version"],
            result_category=row["result_category"],
            created_at=row["created_at"],
        )
        for row in rows
    )


def list_runs(batch_id: str) -> tuple[StoredRun, ...]:
    """Review runs for one batch, oldest first. An unknown batch is an error."""
    with _query() as conn:
        if not _batch_exists(conn, batch_id):
            raise BatchNotFound(f"no batch {batch_id}")
        rows = conn.execute(
            """
            SELECT run_id, batch_id, rule_version, result_category, created_at
            FROM review_run
            WHERE batch_id = %s
            ORDER BY created_at, run_id
            """,
            (batch_id,),
        ).fetchall()
    return tuple(_run(row) for row in rows)


def latest_runs() -> dict[str, StoredRun | None]:
    """Every stored batch and its latest run (None when it has no run).

    Latest is chosen as in list_batches: the greatest created_at, then the
    greater run_id. Keys are in batch_id order.
    """
    with _query() as conn:
        rows = conn.execute(
            """
            SELECT batch.batch_id AS stored_batch_id, latest.*
            FROM batch
            LEFT JOIN LATERAL (
                SELECT run_id, batch_id, rule_version, result_category, created_at
                FROM review_run
                WHERE review_run.batch_id = batch.batch_id
                ORDER BY created_at DESC, run_id DESC
                LIMIT 1
            ) AS latest ON TRUE
            ORDER BY batch.batch_id COLLATE "C"
            """
        ).fetchall()
    return {
        row["stored_batch_id"]: None if row["run_id"] is None else _run(row)
        for row in rows
    }


def list_runs_on(day: date) -> tuple[StoredRun, ...]:
    """Runs whose created_at falls on this calendar day in UTC.

    day is a datetime.date. The comparison uses UTC even when the session
    TimeZone is something else. Oldest first, then batch_id, then run_id.
    """
    if type(day) is not date:
        raise TypeError("day must be a datetime.date")
    with _query() as conn:
        rows = conn.execute(
            """
            SELECT run_id, batch_id, rule_version, result_category, created_at
            FROM review_run
            WHERE (created_at AT TIME ZONE 'UTC')::date = %s
            ORDER BY created_at, batch_id COLLATE "C", run_id
            """,
            (day,),
        ).fetchall()
    return tuple(_run(row) for row in rows)


def list_findings(run_id: str) -> tuple[StoredFinding, ...]:
    """Findings for one run, in finding_seq order. An unknown run is empty."""
    columns = ", ".join(("finding_id", "run_id", "finding_seq", *PAYLOAD_FIELDS))
    with _query() as conn:
        rows = conn.execute(
            f"""
            SELECT {columns}
            FROM finding
            WHERE run_id = %s
            ORDER BY finding_seq
            """,
            (run_id,),
        ).fetchall()
    return tuple(_finding(row) for row in rows)


def list_notes(finding_id: str) -> tuple[StoredNote, ...]:
    """Notes for one finding, oldest first. An unknown finding is empty."""
    with _query() as conn:
        rows = conn.execute(
            """
            SELECT note_id, finding_id, reviewer_name, note_text, noted_at
            FROM finding_note
            WHERE finding_id = %s
            ORDER BY noted_at, note_id
            """,
            (finding_id,),
        ).fetchall()
    return tuple(
        StoredNote(
            note_id=str(row["note_id"]),
            finding_id=str(row["finding_id"]),
            reviewer_name=row["reviewer_name"],
            note_text=row["note_text"],
            noted_at=row["noted_at"],
        )
        for row in rows
    )


def export_batch_csv(batch_id: str) -> str:
    """The auditor CSV for one batch. Raises BatchNotFound when it is not stored.

    Built from list_runs, list_findings, and list_notes. A batch with no
    runs returns the header only.
    """
    runs = list_runs(batch_id)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=EXPORT_COLUMNS,
        lineterminator="\r\n",
        quoting=csv.QUOTE_MINIMAL,
    )
    writer.writeheader()
    for run in runs:
        findings = list_findings(run.run_id)
        if not findings:
            writer.writerow(_blank_finding_row(run))
            continue
        for finding in findings:
            writer.writerow(_finding_row(run, finding, list_notes(finding.finding_id)))
    return buffer.getvalue()


@contextmanager
def _query() -> Iterator[Connection]:
    """One read transaction with dict rows."""
    with transaction() as conn:
        conn.row_factory = dict_row
        yield conn


def _batch_exists(conn: Connection, batch_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM batch WHERE batch_id = %s",
        (batch_id,),
    ).fetchone()
    return row is not None


def _master_step(row: dict[str, object]) -> StoredMasterStep:
    return StoredMasterStep(
        step_seq=_as_int(row["step_seq"]),
        step_id=str(row["step_id"]),
        unit_procedure=str(row["unit_procedure"]),
        operation=str(row["operation"]),
        phase=str(row["phase"]),
        parameter=_text_or_none(row["parameter"]),
        unit=_text_or_none(row["unit"]),
        min_value=_decimal_or_none(row["min_value"]),
        max_value=_decimal_or_none(row["max_value"]),
        critical=bool(row["critical"]),
    )


def _batch_step(row: dict[str, object]) -> StoredBatchStep:
    return StoredBatchStep(
        step_id=str(row["step_id"]),
        reading=_decimal_or_none(row["reading"]),
        unit=_text_or_none(row["unit"]),
        performed_by=_text_or_none(row["performed_by"]),
        performed_at=_time_or_none(row["performed_at"]),
        verified_by=_text_or_none(row["verified_by"]),
        verified_at=_time_or_none(row["verified_at"]),
        comment=_text_or_none(row["comment"]),
    )


def _run(row: dict[str, object]) -> StoredRun:
    created_at = row["created_at"]
    if not isinstance(created_at, datetime):
        raise TypeError("review run time was not a timestamp")
    return StoredRun(
        run_id=str(row["run_id"]),
        batch_id=str(row["batch_id"]),
        rule_version=str(row["rule_version"]),
        result_category=str(row["result_category"]),
        created_at=created_at,
    )


def _finding(row: dict[str, object]) -> StoredFinding:
    values: dict[str, object] = {name: row[name] for name in PAYLOAD_FIELDS}
    values["finding_id"] = str(row["finding_id"])
    values["run_id"] = str(row["run_id"])
    values["finding_seq"] = _as_int(row["finding_seq"])
    return StoredFinding(**values)


def _as_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"expected an integer, got {type(value).__name__}")


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    raise TypeError(f"expected a Decimal, got {type(value).__name__}")


def _time_or_none(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    raise TypeError(f"expected a timestamp, got {type(value).__name__}")


def _blank_finding_row(run: StoredRun) -> dict[str, str]:
    row = dict.fromkeys(EXPORT_COLUMNS, "")
    row["batch_id"] = run.batch_id
    row["run_id"] = run.run_id
    row["reviewed_at"] = _cell(run.created_at)
    row["rule_version"] = run.rule_version
    row["result_category"] = run.result_category
    return row


def _finding_row(
    run: StoredRun,
    finding: StoredFinding,
    notes: Sequence[StoredNote],
) -> dict[str, str]:
    row = _blank_finding_row(run)
    row["step_seq"] = _cell(finding.step_seq)
    row["step_id"] = finding.step_id
    row["unit_procedure"] = finding.unit_procedure
    row["operation"] = finding.operation
    row["phase"] = finding.phase
    row["rule_id"] = finding.rule_id
    row["step_severity"] = finding.step_severity
    row["detail"] = finding.detail
    row["expected_unit"] = _cell(finding.expected_unit)
    row["recorded_unit"] = _cell(finding.recorded_unit)
    row["reading"] = _cell(finding.reading)
    row["min_value"] = _cell(finding.min_value)
    row["max_value"] = _cell(finding.max_value)
    row["performed_by"] = _cell(finding.performed_by)
    row["performed_at"] = _cell(finding.performed_at)
    row["verified_by"] = _cell(finding.verified_by)
    row["verified_at"] = _cell(finding.verified_at)
    row["comment"] = _cell(finding.comment)
    row["notes"] = "\n".join(note.note_text for note in notes)
    return row


def _cell(value: object) -> str:
    """One CSV cell. Decimals keep their scale. Store times are UTC."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.strftime(TIMESTAMP_FORMAT)
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)
