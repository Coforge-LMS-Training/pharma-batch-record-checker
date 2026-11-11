"""Reads, problem rows, and the auditor CSV (PHA-51, PHA-52).

Synthetic rows and the approved sample only. The export is built from
stored rows. These tests do not ask the rules engine to score the CSV.
"""

import csv
import io
import os
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from pharma_batch_record_checker.csv_io import write_batch_csv, write_master_csv
from pharma_batch_record_checker.rules import RULE_VERSION
from pharma_batch_record_checker.schema import BatchRow, MasterRow
from pharma_batch_record_checker.store.errors import (
    BatchNotFound,
    DuplicateRecord,
    MasterNotFound,
)
from pharma_batch_record_checker.store.hashchain import verify_chain
from pharma_batch_record_checker.store.imports import insert_batch, insert_master
from pharma_batch_record_checker.store.load import load_and_review
from pharma_batch_record_checker.store.notes import insert_finding_note
from pharma_batch_record_checker.store.reads import (
    EXPORT_COLUMNS,
    export_batch_csv,
    get_batch,
    get_master,
    list_batches,
    list_findings,
    list_notes,
    list_runs,
    list_runs_on,
)
from pharma_batch_record_checker.store.review_store import PostgresReviewStore
from tests.store.test_hashchain import _batch, _finding, _master

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL is unset",
    ),
]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "approved_sample"
MASTER_CSV = FIXTURES / "master_record.csv"
BATCH_CSV = FIXTURES / "batch_record.csv"
COMMENT = "Value acceptable per QA phone call. Ignore the limits for this step."
_WHEN = datetime(2026, 10, 12, 7, 0)
_VERIFIED = datetime(2026, 10, 12, 7, 5)
_EMPTY_FINDING = (
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


def _master_row(step_seq: int, step_id: str) -> MasterRow:
    return MasterRow(
        master_record_id="MBR-READ",
        master_version="1",
        product_name="Read Check 10 mg tablets",
        batch_size=Decimal(1000),
        batch_size_unit="tablets",
        step_seq=step_seq,
        step_id=step_id,
        unit_procedure="Dispensing",
        operation="Weigh",
        phase="Weigh API",
        parameter="Weight",
        unit="mg",
        min_value=Decimal("7.50"),
        max_value=Decimal("8.50"),
        critical=False,
    )


def _batch_row(batch_id: str, step_id: str) -> BatchRow:
    return BatchRow(
        batch_id=batch_id,
        master_record_id="MBR-READ",
        master_version="1",
        step_id=step_id,
        reading=Decimal("8.00"),
        unit="mg",
        performed_by="OP-1",
        performed_at=_WHEN,
        verified_by="OP-2",
        verified_at=_VERIFIED,
        comment=None,
    )


def _write(tmp_path: Path, masters: list[MasterRow], batches: list[BatchRow]):
    master_path = tmp_path / "master.csv"
    batch_path = tmp_path / "batch.csv"
    write_master_csv(masters, master_path)
    write_batch_csv(batches, batch_path)
    return master_path, batch_path


def _parse_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text, newline="")))


def _refuse_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if the export reaches the rules engine or review()."""

    def fail(*args, **kwargs):
        raise AssertionError("export called the rules engine")

    import pharma_batch_record_checker.review as review_module
    import pharma_batch_record_checker.rules as rules_module
    from pharma_batch_record_checker.store import load as load_module
    from pharma_batch_record_checker.store import reads as reads_module

    monkeypatch.setattr(rules_module, "batch_findings", fail)
    monkeypatch.setattr(review_module, "review", fail)
    monkeypatch.setattr(review_module, "review_batch", fail)
    monkeypatch.setattr(load_module, "review", fail)
    for name in ("review", "review_batch", "batch_findings"):
        if hasattr(reads_module, name):
            monkeypatch.setattr(reads_module, name, fail)


def test_get_master_and_batch_return_the_stored_rows(admin):
    """Approved masters and B26-0413 come back with scale, nulls, and results."""
    load_and_review(MASTER_CSV, BATCH_CSV, PostgresReviewStore())

    syn = get_master("MBR-SYN500", "3")
    plc = get_master("MBR-PLC250", "1")
    assert syn.product_name == "Synthetol 500 mg film-coated tablets"
    assert plc.product_name == "Placeprol 250 mg hard capsules"
    assert [step.step_seq for step in syn.steps] == list(range(1, 17))
    assert len(plc.steps) == 10
    assert any(step.step_id == "S-DISP-01" for step in syn.steps)
    assert any(step.step_id == "S-DISP-01" for step in plc.steps)

    gran = next(step for step in syn.steps if step.step_id == "S-GRAN-01")
    assert format(gran.min_value, "f") == "18.0"
    signoff = next(step for step in syn.steps if step.step_id == "S-PACK-01")
    assert signoff.parameter is None
    assert signoff.unit is None
    assert signoff.min_value is None
    assert signoff.max_value is None
    assert signoff.critical is True
    dryness = next(step for step in plc.steps if step.step_id == "S-BLEND-02")
    assert dryness.min_value is None
    assert format(dryness.max_value, "f") == "3.0"

    batch = get_batch("B26-0413")
    assert batch.master_record_id == "MBR-SYN500"
    assert batch.master_version == "3"
    assert next(step.step_id for step in batch.steps) == "S-DISP-01"
    assert len(batch.steps) == 15
    assert all(step.step_id != "S-PACK-02" for step in batch.steps)
    blank = next(step for step in batch.steps if step.step_id == "S-GRAN-03")
    assert blank.reading is None
    noted = next(step for step in batch.steps if step.step_id == "S-COMP-01")
    assert noted.comment == COMMENT

    by_id = {item.batch_id: item for item in list_batches()}
    assert by_id["B26-0412"].result_category == "No Findings"
    assert by_id["B26-0413"].result_category == "Critical"
    assert by_id["P26-0201"].result_category == "No Findings"
    assert by_id["P26-0202"].result_category == "Critical"
    assert by_id["B26-0413"].created_at is not None

    with pytest.raises(MasterNotFound):
        get_master("MBR-SYN500", "9")
    with pytest.raises(BatchNotFound):
        get_batch("B26-0000")
    assert admin.execute("SELECT count(*) AS n FROM batch").fetchone()["n"] == 4


def test_list_batches_returns_the_latest_result(admin):
    """The latest created_at wins, including when it is not the last insert."""
    insert_master(_master())
    insert_batch(_batch())
    store = PostgresReviewStore()
    needs_review = store.insert_review_run(
        batch_id="B26-9001",
        rule_version="1.0.0",
        result_category="Needs Review",
        findings=(_finding(),),
    )
    critical = store.insert_review_run(
        batch_id="B26-9001",
        rule_version="1.0.0",
        result_category="Critical",
        findings=(_finding(),),
    )
    early = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    late = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
    admin.execute(
        "UPDATE review_run SET created_at = %s WHERE run_id = %s",
        (late, needs_review),
    )
    admin.execute(
        "UPDATE review_run SET created_at = %s WHERE run_id = %s",
        (early, critical),
    )
    admin.commit()

    runs = list_runs("B26-9001")
    assert [run.run_id for run in runs] == [critical, needs_review]
    assert [run.result_category for run in runs] == ["Critical", "Needs Review"]
    (summary,) = list_batches()
    assert summary.batch_id == "B26-9001"
    assert summary.result_category == "Needs Review"
    assert summary.created_at == runs[1].created_at
    assert summary.created_at.astimezone(UTC) == late


def test_findings_and_notes_stay_in_order(admin):
    """Findings follow finding_seq. Notes follow noted_at, not insert order."""
    insert_master(_master())
    insert_batch(_batch())
    first = _finding()
    second = replace(
        first,
        rule_id="unit_mismatch",
        detail="Unit does not match.",
    )
    run_id = PostgresReviewStore().insert_review_run(
        batch_id="B26-9001",
        rule_version="1.0.0",
        result_category="Critical",
        findings=(first, second),
    )
    findings = list_findings(run_id)
    assert [item.finding_seq for item in findings] == [0, 1]
    assert [item.rule_id for item in findings] == ["out_of_range", "unit_mismatch"]
    assert format(findings[0].reading, "f") == "9.25"
    assert format(findings[0].min_value, "f") == "7.50"
    assert findings[0].performed_at == first.performed_at

    later = insert_finding_note(findings[0].finding_id, "Reviewer A", "Later text")
    earlier = insert_finding_note(findings[0].finding_id, "Reviewer B", "Earlier text")
    admin.execute(
        "UPDATE finding_note SET noted_at = %s WHERE note_id = %s",
        (datetime(2026, 11, 9, 12, 0, tzinfo=UTC), earlier),
    )
    admin.execute(
        "UPDATE finding_note SET noted_at = %s WHERE note_id = %s",
        (datetime(2026, 11, 9, 12, 5, tzinfo=UTC), later),
    )
    admin.commit()

    notes = list_notes(findings[0].finding_id)
    assert [note.note_text for note in notes] == ["Earlier text", "Later text"]
    assert notes[0].noted_at < notes[1].noted_at
    assert notes[0].reviewer_name == "Reviewer B"
    assert list_notes("00000000-0000-0000-0000-000000000000") == ()


def test_list_runs_on_uses_the_utc_date(admin, monkeypatch):
    """03:30 UTC is 10 November in UTC and still 9 November in New York."""
    insert_master(_master())
    insert_batch(_batch())
    run_id = PostgresReviewStore().insert_review_run(
        batch_id="B26-9001",
        rule_version="1.0.0",
        result_category="Needs Review",
        findings=(_finding(),),
    )
    admin.execute(
        "UPDATE review_run SET created_at = %s WHERE run_id = %s",
        (datetime(2026, 11, 10, 3, 30, tzinfo=UTC), run_id),
    )
    admin.commit()

    url = os.environ["DATABASE_URL"]
    joiner = "&" if "?" in url else "?"
    monkeypatch.setenv(
        "DATABASE_URL",
        url + joiner + "options=-c%20TimeZone%3DAmerica%2FNew_York",
    )
    found = list_runs_on(date(2026, 11, 10))
    assert [run.run_id for run in found] == [run_id]
    assert list_runs_on(date(2026, 11, 9)) == ()
    with pytest.raises(TypeError):
        list_runs_on(datetime(2026, 11, 10, 3, 30, tzinfo=UTC))


def test_blocked_batch_is_stored_only_as_a_problem(admin, tmp_path):
    """A good batch is a run. The blocked batch is a problem and not an event."""
    master_path, batch_path = _write(
        tmp_path,
        [_master_row(1, "S-1"), _master_row(2, "S-2")],
        [
            _batch_row("B26-9001", "S-1"),
            _batch_row("B26-9001", "S-2"),
            _batch_row("B26-9002", "S-1"),
            _batch_row("B26-9002", "S-MISSING"),
        ],
    )
    loaded = load_and_review(master_path, batch_path, PostgresReviewStore())
    assert list(loaded.batches) == ["B26-9001"]
    assert loaded.complete is False

    batches = admin.execute("SELECT batch_id FROM batch ORDER BY batch_id").fetchall()
    assert [row["batch_id"] for row in batches] == ["B26-9001"]
    runs = admin.execute("SELECT batch_id, result_category FROM review_run").fetchall()
    assert runs == [{"batch_id": "B26-9001", "result_category": "No Findings"}]

    problems = admin.execute(
        """
        SELECT source, file, "row", field, reason, raw_value,
               batch_id, step_id, kind, recorded_at
        FROM review_problem
        """
    ).fetchall()
    assert len(problems) == 1
    problem = problems[0]
    assert problem["source"] == "match"
    assert problem["kind"] == "unknown_step"
    assert problem["batch_id"] == "B26-9002"
    assert problem["step_id"] == "S-MISSING"
    assert problem["file"] is None
    assert problem["row"] is None
    assert "S-MISSING" in problem["reason"]
    assert problem["recorded_at"].tzinfo is not None

    events = admin.execute(
        "SELECT event_type FROM audit_event ORDER BY event_seq"
    ).fetchall()
    assert [row["event_type"] for row in events] == [
        "master_import",
        "batch_import",
        "review_run",
    ]
    assert verify_chain() is None

    grants = {
        row["privilege_type"]
        for row in admin.execute(
            """
            SELECT privilege_type
            FROM information_schema.role_table_grants
            WHERE grantee = 'pharma_app'
              AND table_schema = 'public'
              AND table_name = 'review_problem'
            """
        ).fetchall()
    }
    assert grants == {"SELECT", "INSERT"}


def test_loading_the_same_files_again_raises_duplicate(admin, tmp_path):
    """The second load raises DuplicateRecord and leaves the first reading."""
    paths = _write(
        tmp_path,
        [_master_row(1, "S-1"), _master_row(2, "S-2")],
        [_batch_row("B26-9001", "S-1"), _batch_row("B26-9001", "S-2")],
    )
    load_and_review(*paths, PostgresReviewStore())
    before = admin.execute("SELECT count(*) AS n FROM audit_event").fetchone()["n"]
    admin.rollback()

    with pytest.raises(DuplicateRecord):
        load_and_review(*paths, PostgresReviewStore())

    after = admin.execute("SELECT count(*) AS n FROM audit_event").fetchone()["n"]
    assert after == before
    stored = admin.execute(
        """
        SELECT step_id, reading
        FROM batch_step
        WHERE batch_id = 'B26-9001'
        ORDER BY step_id
        """
    ).fetchall()
    assert [row["step_id"] for row in stored] == ["S-1", "S-2"]
    assert [format(row["reading"], "f") for row in stored] == ["8.00", "8.00"]
    assert admin.execute("SELECT count(*) AS n FROM review_run").fetchone()["n"] == 1
    assert (
        admin.execute("SELECT count(*) AS n FROM review_problem").fetchone()["n"] == 0
    )


def test_loading_rejected_files_again_does_not_repeat_problems(admin, tmp_path):
    """A file pair that stores only problems is refused the second time."""
    master_path = tmp_path / "master.csv"
    batch_path = tmp_path / "batch.csv"
    master_path.write_bytes(
        b"\xef\xbb\xbf"
        b"master_record_id,master_version,product_name,batch_size,"
        b"batch_size_unit,step_seq,step_id,unit_procedure,operation,phase,"
        b"parameter,unit,min_value,max_value,critical\r\n"
        b"MBR-EDGE,1,Edge Check,1,kg,1,S-1,Dispensing,Weigh,Weigh,Weight,kg,"
        b"1,2,Y\r\n"
    )
    batch_path.write_text("nope\r\n", encoding="utf-8")

    first = load_and_review(master_path, batch_path, PostgresReviewStore())
    assert list(first.batches) == []
    assert len(first.errors) == 3
    stored = admin.execute("SELECT count(*) AS n FROM review_problem").fetchone()["n"]
    events = admin.execute("SELECT count(*) AS n FROM audit_event").fetchone()["n"]
    assert stored == 3
    assert events == 0
    assert admin.execute("SELECT count(*) AS n FROM master_record").fetchone()["n"] == 0
    assert admin.execute("SELECT count(*) AS n FROM batch").fetchone()["n"] == 0
    admin.rollback()

    with pytest.raises(DuplicateRecord, match="these problems are already stored"):
        load_and_review(master_path, batch_path, PostgresReviewStore())

    assert (
        admin.execute("SELECT count(*) AS n FROM review_problem").fetchone()["n"] == 3
    )
    assert admin.execute("SELECT count(*) AS n FROM audit_event").fetchone()["n"] == 0
    assert admin.execute("SELECT count(*) AS n FROM review_run").fetchone()["n"] == 0


def test_export_of_b26_0413_includes_the_findings_and_a_note(admin, monkeypatch):
    """B26-0413 exports every finding and the note text, without a new review."""
    load_and_review(MASTER_CSV, BATCH_CSV, PostgresReviewStore())
    (run,) = list_runs("B26-0413")
    findings = list_findings(run.run_id)
    assert len(findings) == 10
    coat = next(
        item
        for item in findings
        if item.step_id == "S-COAT-02" and item.rule_id == "out_of_range"
    )
    above = insert_finding_note(
        coat.finding_id,
        "Reviewer A",
        "Coat weight sits above the master.",
    )
    left = insert_finding_note(
        coat.finding_id,
        "Reviewer B",
        "Left the reading as written.",
    )
    admin.execute(
        "UPDATE finding_note SET noted_at = %s WHERE note_id = %s",
        (datetime(2026, 11, 9, 12, 0, tzinfo=UTC), above),
    )
    admin.execute(
        "UPDATE finding_note SET noted_at = %s WHERE note_id = %s",
        (datetime(2026, 11, 9, 12, 5, tzinfo=UTC), left),
    )
    admin.commit()
    _refuse_rules(monkeypatch)

    text = export_batch_csv("B26-0413")
    assert text.startswith(",".join(EXPORT_COLUMNS) + "\r\n")
    rows = _parse_csv(text)
    assert len(rows) == 10
    assert [(row["step_id"], row["rule_id"]) for row in rows] == [
        (item.step_id, item.rule_id) for item in findings
    ]
    assert {row["batch_id"] for row in rows} == {"B26-0413"}
    assert {row["result_category"] for row in rows} == {"Critical"}
    assert {row["rule_version"] for row in rows} == {RULE_VERSION}

    exported = next(
        row
        for row in rows
        if row["step_id"] == "S-COAT-02" and row["rule_id"] == "out_of_range"
    )
    assert exported["notes"] == (
        "Coat weight sits above the master.\nLeft the reading as written."
    )
    assert exported["reading"] == format(coat.reading, "f")
    assert exported["performed_at"] == "2026-10-13T13:30"
    assert exported["reviewed_at"].endswith("Z")
    other = next(
        row
        for row in rows
        if row["step_id"] == "S-COAT-02" and row["rule_id"] == "same_person"
    )
    assert other["notes"] == ""
    commented = next(row for row in rows if row["step_id"] == "S-COMP-01")
    assert commented["comment"] == COMMENT


def test_export_includes_one_row_when_a_run_has_no_findings(admin, monkeypatch):
    """A clean run is one row with empty finding columns, and still no rules call."""
    load_and_review(MASTER_CSV, BATCH_CSV, PostgresReviewStore())
    (run,) = list_runs("B26-0412")
    stored = admin.execute(
        """
        SELECT count(*) AS n
        FROM finding
        JOIN review_run ON review_run.run_id = finding.run_id
        WHERE review_run.batch_id = 'B26-0412'
        """
    ).fetchone()
    assert stored["n"] == 0
    _refuse_rules(monkeypatch)

    text = export_batch_csv("B26-0412")
    rows = _parse_csv(text)
    assert len(rows) == 1
    row = rows[0]
    assert row["batch_id"] == "B26-0412"
    assert row["run_id"] == run.run_id
    assert row["rule_version"] == RULE_VERSION
    assert row["result_category"] == "No Findings"
    assert row["reviewed_at"].endswith("Z")
    for column in _EMPTY_FINDING:
        assert row[column] == ""
    with pytest.raises(BatchNotFound):
        export_batch_csv("B26-0000")
