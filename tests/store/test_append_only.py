"""Append-only triggers (PHA-49). The app role keeps SELECT and INSERT.

A temporary UPDATE or DELETE grant is how these tests reach the trigger.
Without it, Postgres rejects the statement before the trigger runs.
pharma_admin can still change a row. verify_chain is what notices.
"""

import os
from decimal import Decimal

import psycopg
import pytest

from pharma_batch_record_checker.store.hashchain import ChainFailure, verify_chain
from pharma_batch_record_checker.store.imports import insert_batch, insert_master
from pharma_batch_record_checker.store.review_store import PostgresReviewStore
from tests.store.test_hashchain import _batch, _finding, _master

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL is unset",
    ),
]


def _app():
    return psycopg.connect(os.environ["DATABASE_URL"])


def _grant(admin, table: str) -> None:
    admin.execute(f"GRANT UPDATE, DELETE ON TABLE {table} TO pharma_app")
    admin.commit()


def _revoke(admin, table: str) -> None:
    admin.rollback()
    admin.execute(f"REVOKE UPDATE, DELETE ON TABLE {table} FROM pharma_app")
    admin.commit()


def test_app_cannot_update_or_delete_a_batch_step(admin):
    """A granted UPDATE or DELETE on a batch step still raises append-only."""
    insert_master(_master())
    insert_batch(_batch())
    _grant(admin, "batch_step")
    try:
        with _app() as app:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                app.execute(
                    """
                    UPDATE batch_step
                    SET reading = 1
                    WHERE batch_id = 'B26-9001' AND step_id = 'S-1'
                    """
                )
            app.rollback()
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                app.execute(
                    """
                    DELETE FROM batch_step
                    WHERE batch_id = 'B26-9001' AND step_id = 'S-1'
                    """
                )
            app.rollback()
    finally:
        _revoke(admin, "batch_step")

    stored = admin.execute(
        """
        SELECT reading FROM batch_step
        WHERE batch_id = 'B26-9001' AND step_id = 'S-1'
        """
    ).fetchone()
    assert format(stored["reading"], "f") == "9.25"
    privileges = {
        row["privilege_type"]
        for row in admin.execute(
            """
            SELECT privilege_type
            FROM information_schema.role_table_grants
            WHERE grantee = 'pharma_app'
              AND table_schema = 'public'
              AND table_name = 'batch_step'
            """
        ).fetchall()
    }
    assert "UPDATE" not in privileges
    assert "DELETE" not in privileges


def test_app_cannot_delete_a_finding(admin):
    """A granted DELETE on a finding still raises append-only."""
    insert_master(_master())
    insert_batch(_batch())
    PostgresReviewStore().insert_review_run(
        batch_id="B26-9001",
        rule_version="1.0.0",
        result_category="Needs Review",
        findings=(_finding(),),
    )
    _grant(admin, "finding")
    try:
        with _app() as app:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                app.execute("DELETE FROM finding")
            app.rollback()
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                app.execute("UPDATE finding SET detail = 'changed'")
            app.rollback()
    finally:
        _revoke(admin, "finding")

    assert admin.execute("SELECT count(*) AS n FROM finding").fetchone()["n"] == 1


def test_app_can_still_insert_a_batch_step(admin):
    """INSERT is still allowed, and the store path still verifies."""
    insert_master(_master())
    insert_batch(_batch())
    assert verify_chain() is None

    with _app() as app:
        app.execute(
            """
            INSERT INTO batch_step (
                batch_id, step_id, reading, unit, performed_by, performed_at,
                verified_by, verified_at, comment
            ) VALUES (
                'B26-9001', 'S-2', NULL, NULL, NULL, NULL, NULL, NULL, NULL
            )
            """
        )
        app.commit()

    stored = admin.execute(
        """
        SELECT reading FROM batch_step
        WHERE batch_id = 'B26-9001' AND step_id = 'S-2'
        """
    ).fetchone()
    assert stored["reading"] is None


def test_admin_change_is_caught_by_verify_chain(admin):
    """pharma_admin can change a reading. verify_chain reports that batch event."""
    insert_master(_master())
    insert_batch(_batch())
    original = admin.execute(
        """
        SELECT reading FROM batch_step
        WHERE batch_id = 'B26-9001' AND step_id = 'S-1'
        """
    ).fetchone()["reading"]
    admin.execute(
        """
        UPDATE batch_step
        SET reading = %s
        WHERE batch_id = 'B26-9001' AND step_id = 'S-1'
        """,
        (Decimal(1),),
    )
    admin.commit()

    assert verify_chain() == ChainFailure(2, "payload_hash")

    admin.execute(
        """
        UPDATE batch_step
        SET reading = %s
        WHERE batch_id = 'B26-9001' AND step_id = 'S-1'
        """,
        (original,),
    )
    admin.commit()
    assert verify_chain() is None
