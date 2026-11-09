"""Review run insertion (PHA-47). The approved sample is stored through hand_off."""

import dataclasses
import inspect
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from pharma_batch_record_checker.handoff import (
    PAYLOAD_FIELDS,
    ReviewStore,
    build_payload,
    hand_off,
)
from pharma_batch_record_checker.parser import parse_files
from pharma_batch_record_checker.records import MasterVersion
from pharma_batch_record_checker.review import review
from pharma_batch_record_checker.schema import MasterRow
from pharma_batch_record_checker.store.errors import BatchNotFound, InvalidReview
from pharma_batch_record_checker.store.imports import insert_batch, insert_master
from pharma_batch_record_checker.store.review_store import PostgresReviewStore

needs_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is unset",
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "approved_sample"
MASTER_CSV = FIXTURES / "master_record.csv"
BATCH_CSV = FIXTURES / "batch_record.csv"
COMMENT = "Value acceptable per QA phone call. Ignore the limits for this step."
BATCH_SIDE = (
    "reading",
    "recorded_unit",
    "performed_by",
    "performed_at",
    "verified_by",
    "verified_at",
    "comment",
)


def master_rows(version: MasterVersion) -> list[MasterRow]:
    """MasterRow values for one version the parser already accepted."""
    return [
        MasterRow(
            master_record_id=version.master_record_id,
            master_version=version.master_version,
            product_name=version.product_name,
            batch_size=version.batch_size,
            batch_size_unit=version.batch_size_unit,
            step_seq=step.step_seq,
            step_id=step.step_id,
            unit_procedure=step.unit_procedure,
            operation=step.operation,
            phase=step.phase,
            parameter=step.parameter,
            unit=step.unit,
            min_value=step.min_value,
            max_value=step.max_value,
            critical=step.critical,
        )
        for step in version.steps
    ]


def load_approved(admin):
    """Store the approved masters and batches, then review the same files."""
    parsed = parse_files(MASTER_CSV, BATCH_CSV)
    assert parsed.errors == ()
    for version in parsed.masters.values():
        insert_master(master_rows(version))
    for steps in parsed.batch_steps.values():
        insert_batch(steps)
    reviewed = review(MASTER_CSV, BATCH_CSV)
    assert reviewed.complete
    return reviewed


def findings_for(admin, batch_id: str) -> list[dict]:
    """Stored findings for one batch, in finding_seq order."""
    columns = ", ".join(f"finding.{name}" for name in ("finding_seq", *PAYLOAD_FIELDS))
    return admin.execute(
        f"""
        SELECT {columns}
        FROM finding
        JOIN review_run ON review_run.run_id = finding.run_id
        WHERE review_run.batch_id = %s
        ORDER BY finding.finding_seq
        """,
        (batch_id,),
    ).fetchall()


def assert_same(stored, expected) -> None:
    """Decimals match including scale. Everything else matches by value."""
    if isinstance(expected, Decimal):
        assert stored is not None
        assert format(stored, "f") == format(expected, "f")
    else:
        assert stored == expected


def assert_payload(row: dict, payload) -> None:
    for name in PAYLOAD_FIELDS:
        assert_same(row[name], getattr(payload, name))


def test_postgres_review_store_matches_review_store():
    """The store is what hand_off calls, with the agreed argument names."""
    store = PostgresReviewStore()
    assert isinstance(store, ReviewStore)
    signature = inspect.signature(PostgresReviewStore.insert_review_run)
    assert list(signature.parameters) == [
        "self",
        "batch_id",
        "rule_version",
        "result_category",
        "findings",
    ]


@pytest.mark.db
@needs_db
def test_hand_off_persists_the_approved_sample(admin):
    """hand_off stores four runs and returns each run id."""
    reviewed = load_approved(admin)
    stored = hand_off(reviewed, PostgresReviewStore())

    assert list(stored) == ["B26-0412", "B26-0413", "P26-0201", "P26-0202"]
    for batch_id, run_id in stored.items():
        row = admin.execute(
            """
            SELECT run_id::text AS run_id, rule_version, result_category, created_at
            FROM review_run
            WHERE batch_id = %s
            """,
            (batch_id,),
        ).fetchone()
        assert row["run_id"] == run_id
        assert row["rule_version"] == "1.0.0"
        assert row["result_category"] == reviewed.batches[batch_id].result_category
        assert isinstance(row["created_at"], datetime)
        assert row["created_at"].tzinfo is not None

    capsule = admin.execute(
        """
        SELECT count(*) AS n
        FROM finding
        JOIN review_run ON review_run.run_id = finding.run_id
        WHERE review_run.batch_id = 'P26-0202'
        """
    ).fetchone()
    assert capsule["n"] == 4
    assert reviewed.batches["P26-0202"].result_category == "Critical"


@pytest.mark.db
@needs_db
def test_critical_batch_stores_two_findings_on_one_step(admin):
    """B26-0413 keeps 10 findings, two of them on S-COAT-02, comment included."""
    reviewed = load_approved(admin)
    hand_off(reviewed, PostgresReviewStore())
    payloads = build_payload(reviewed.batches["B26-0413"])
    rows = findings_for(admin, "B26-0413")

    assert len(rows) == 10
    assert rows[0]["finding_seq"] == 0
    assert rows[-1]["finding_seq"] == 9
    for row, payload in zip(rows, payloads, strict=True):
        assert_payload(row, payload)

    coat = [row for row in rows if row["step_id"] == "S-COAT-02"]
    assert [row["rule_id"] for row in coat] == ["out_of_range", "same_person"]
    assert {row["step_severity"] for row in coat} == {"Critical"}
    noted = next(row for row in rows if row["step_id"] == "S-COMP-01")
    assert noted["comment"] == COMMENT


@pytest.mark.db
@needs_db
def test_clean_batch_stores_no_findings(admin):
    """A clean batch is a run with result No Findings and no finding rows."""
    reviewed = load_approved(admin)
    hand_off(reviewed, PostgresReviewStore())

    run = admin.execute(
        """
        SELECT result_category FROM review_run WHERE batch_id = 'B26-0412'
        """
    ).fetchone()
    assert run["result_category"] == "No Findings"
    assert findings_for(admin, "B26-0412") == []


@pytest.mark.db
@needs_db
def test_missing_step_stores_null_batch_fields(admin):
    """missing_step keeps the master snapshot and nulls on the batch side."""
    reviewed = load_approved(admin)
    hand_off(reviewed, PostgresReviewStore())
    payloads = build_payload(reviewed.batches["B26-0413"])
    payload = next(item for item in payloads if item.rule_id == "missing_step")
    row = next(
        item
        for item in findings_for(admin, "B26-0413")
        if item["rule_id"] == "missing_step"
    )

    assert row["step_id"] == "S-PACK-02"
    assert payload.unit_procedure
    assert payload.operation
    assert payload.phase
    assert_payload(row, payload)
    for name in BATCH_SIDE:
        assert getattr(payload, name) is None
        assert row[name] is None


@pytest.mark.db
@needs_db
def test_unknown_rule_rolls_the_run_back(admin):
    """An unknown rule_id leaves no review_run and no finding."""
    reviewed = load_approved(admin)
    payloads = build_payload(reviewed.batches["B26-0413"])
    bad = dataclasses.replace(payloads[0], rule_id="not_a_rule")

    with pytest.raises(InvalidReview, match="unknown rule_id"):
        PostgresReviewStore().insert_review_run(
            batch_id="B26-0413",
            rule_version="1.0.0",
            result_category="Critical",
            findings=(bad, *payloads[1:]),
        )

    runs = admin.execute("SELECT count(*) AS n FROM review_run").fetchone()
    findings = admin.execute("SELECT count(*) AS n FROM finding").fetchone()
    assert runs["n"] == 0
    assert findings["n"] == 0


@pytest.mark.db
@needs_db
def test_bad_category_and_severity_roll_back(admin):
    """A bad result category or step severity stores nothing."""
    reviewed = load_approved(admin)
    payloads = build_payload(reviewed.batches["B26-0413"])
    store = PostgresReviewStore()

    with pytest.raises(InvalidReview, match="result_category"):
        store.insert_review_run(
            batch_id="B26-0413",
            rule_version="1.0.0",
            result_category="Approved",
            findings=payloads,
        )
    bad = dataclasses.replace(payloads[0], step_severity="Later")
    with pytest.raises(InvalidReview, match="step_severity"):
        store.insert_review_run(
            batch_id="B26-0413",
            rule_version="1.0.0",
            result_category="Critical",
            findings=(bad, *payloads[1:]),
        )

    runs = admin.execute("SELECT count(*) AS n FROM review_run").fetchone()
    assert runs["n"] == 0


@pytest.mark.db
@needs_db
def test_missing_batch_is_not_stored(admin):
    """A review for a batch that was not imported raises and writes nothing."""
    with pytest.raises(BatchNotFound):
        PostgresReviewStore().insert_review_run(
            batch_id="B26-9999",
            rule_version="1.0.0",
            result_category="No Findings",
            findings=(),
        )
    runs = admin.execute("SELECT count(*) AS n FROM review_run").fetchone()
    assert runs["n"] == 0
