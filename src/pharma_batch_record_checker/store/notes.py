"""Append a reviewer note on one finding.

The database sets noted_at. The caller does not supply it. The note and
its hash-chain event commit together. The finding row is not updated.
"""

import uuid

import psycopg

from pharma_batch_record_checker.store.connection import transaction
from pharma_batch_record_checker.store.errors import FindingNotFound
from pharma_batch_record_checker.store.hashchain import FINDING_NOTE, append_event


def insert_finding_note(finding_id: str, reviewer_name: str, note_text: str) -> str:
    """Store one note and return its id.

    reviewer_name and note_text are stored as given. Each must contain a
    non-whitespace character. An unknown finding stores nothing.
    """
    _require_text(reviewer_name, "reviewer_name")
    _require_text(note_text, "note_text")
    note_id = uuid.uuid4()
    try:
        with transaction() as conn:
            if not _finding_exists(conn, finding_id):
                raise FindingNotFound(f"no finding {finding_id}")
            conn.execute(
                """
                INSERT INTO finding_note (
                    note_id, finding_id, reviewer_name, note_text
                ) VALUES (%s, %s, %s, %s)
                """,
                (note_id, finding_id, reviewer_name, note_text),
            )
            append_event(conn, FINDING_NOTE, str(note_id))
    except psycopg.errors.ForeignKeyViolation as exc:
        raise FindingNotFound(f"no finding {finding_id}") from exc
    except psycopg.errors.CheckViolation as exc:
        raise ValueError("reviewer_name and note_text must not be blank") from exc
    return str(note_id)


def _require_text(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    if value.strip() == "":
        raise ValueError(f"{field} must not be blank")


def _finding_exists(conn: psycopg.Connection, finding_id: str) -> bool:
    try:
        uuid.UUID(finding_id)
    except ValueError:
        return False
    row = conn.execute(
        "SELECT 1 FROM finding WHERE finding_id = %s",
        (finding_id,),
    ).fetchone()
    return row is not None
