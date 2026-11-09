"""Reviewer notes (PHA-48). The database sets noted_at. Synthetic rows only."""

import inspect
import os
import uuid
from datetime import datetime

import psycopg
import pytest

from pharma_batch_record_checker.store.errors import FindingNotFound
from pharma_batch_record_checker.store.hashchain import ChainFailure, verify_chain
from pharma_batch_record_checker.store.imports import insert_batch, insert_master
from pharma_batch_record_checker.store.notes import insert_finding_note
from pharma_batch_record_checker.store.review_store import PostgresReviewStore
from tests.store.test_hashchain import _batch, _finding, _master

needs_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is unset",
)


def test_insert_finding_note_does_not_take_a_timestamp():
    """Callers do not supply noted_at. The column default is the server clock."""
    assert list(inspect.signature(insert_finding_note).parameters) == [
        "finding_id",
        "reviewer_name",
        "note_text",
    ]


def _stored_finding(admin) -> str:
    """One finding with reading 9.25, and the chain events for its load."""
    insert_master(_master())
    insert_batch(_batch())
    PostgresReviewStore().insert_review_run(
        batch_id="B26-9001",
        rule_version="1.0.0",
        result_category="Needs Review",
        findings=(_finding(),),
    )
    return admin.execute(
        "SELECT finding_id::text AS finding_id FROM finding"
    ).fetchone()["finding_id"]


@pytest.mark.db
@needs_db
def test_two_notes_leave_the_finding_unchanged(admin):
    """Two notes keep the finding, set noted_at, and extend a chain that verifies."""
    finding_id = _stored_finding(admin)
    before = admin.execute("SELECT * FROM finding").fetchone()

    first = insert_finding_note(
        finding_id,
        "Reviewer A",
        "Checked the weighing step.",
    )
    second = insert_finding_note(
        finding_id,
        "Reviewer B",
        'He said "do not change the reading".',
    )

    after = admin.execute("SELECT * FROM finding").fetchone()
    assert after == before
    assert format(after["reading"], "f") == "9.25"

    notes = admin.execute(
        """
        SELECT note_id::text AS note_id, reviewer_name, note_text, noted_at
        FROM finding_note
        ORDER BY noted_at, note_id
        """
    ).fetchall()
    assert [row["note_id"] for row in notes] == [first, second]
    assert notes[0]["reviewer_name"] == "Reviewer A"
    assert notes[1]["note_text"] == 'He said "do not change the reading".'
    assert isinstance(notes[0]["noted_at"], datetime)
    assert notes[0]["noted_at"].tzinfo is not None
    assert notes[1]["noted_at"] >= notes[0]["noted_at"]

    assert verify_chain() is None
    events = admin.execute(
        """
        SELECT event_type, entity_id
        FROM audit_event
        WHERE event_type = 'finding_note'
        ORDER BY event_seq
        """
    ).fetchall()
    assert [row["entity_id"] for row in events] == [first, second]


@pytest.mark.db
@needs_db
def test_blank_note_is_rejected(admin):
    """A blank name or text stores no note and no event."""
    finding_id = _stored_finding(admin)

    with pytest.raises(ValueError, match="reviewer_name"):
        insert_finding_note(finding_id, "   ", "A note.")
    with pytest.raises(ValueError, match="note_text"):
        insert_finding_note(finding_id, "Reviewer A", "")

    assert admin.execute("SELECT count(*) AS n FROM finding_note").fetchone()["n"] == 0
    assert (
        admin.execute(
            """
            SELECT count(*) AS n FROM audit_event
            WHERE event_type = 'finding_note'
            """
        ).fetchone()["n"]
        == 0
    )
    assert verify_chain() is None


@pytest.mark.db
@needs_db
def test_unknown_finding_stores_nothing(admin):
    """A missing finding raises and leaves the chain where it was."""
    _stored_finding(admin)

    with pytest.raises(FindingNotFound):
        insert_finding_note(str(uuid.uuid4()), "Reviewer A", "Not stored.")
    with pytest.raises(FindingNotFound):
        insert_finding_note("not-a-uuid", "Reviewer A", "Not stored.")

    assert admin.execute("SELECT count(*) AS n FROM finding_note").fetchone()["n"] == 0
    assert (
        admin.execute(
            """
            SELECT count(*) AS n FROM audit_event
            WHERE event_type = 'finding_note'
            """
        ).fetchone()["n"]
        == 0
    )


@pytest.mark.db
@needs_db
def test_app_cannot_change_a_note_and_admin_edit_breaks_the_chain(admin):
    """The app cannot update a note. An admin edit is reported, then restored."""
    finding_id = _stored_finding(admin)
    note_id = insert_finding_note(finding_id, "Reviewer A", "Original note.")
    admin.execute("GRANT UPDATE, DELETE ON TABLE finding_note TO pharma_app")
    admin.commit()
    try:
        with psycopg.connect(os.environ["DATABASE_URL"]) as app:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                app.execute(
                    """
                    UPDATE finding_note
                    SET note_text = 'changed'
                    WHERE note_id = %s
                    """,
                    (note_id,),
                )
            app.rollback()
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                app.execute(
                    "DELETE FROM finding_note WHERE note_id = %s",
                    (note_id,),
                )
            app.rollback()
    finally:
        admin.rollback()
        admin.execute("REVOKE UPDATE, DELETE ON TABLE finding_note FROM pharma_app")
        admin.commit()

    assert (
        admin.execute(
            "SELECT note_text FROM finding_note WHERE note_id = %s",
            (note_id,),
        ).fetchone()["note_text"]
        == "Original note."
    )

    admin.execute(
        """
        UPDATE finding_note
        SET note_text = 'Tampered note.'
        WHERE note_id = %s
        """,
        (note_id,),
    )
    admin.commit()
    event_seq = admin.execute(
        "SELECT event_seq FROM audit_event WHERE entity_id = %s",
        (note_id,),
    ).fetchone()["event_seq"]
    assert verify_chain() == ChainFailure(event_seq, "payload_hash")

    admin.execute(
        """
        UPDATE finding_note
        SET note_text = 'Original note.'
        WHERE note_id = %s
        """,
        (note_id,),
    )
    admin.commit()
    assert verify_chain() is None
