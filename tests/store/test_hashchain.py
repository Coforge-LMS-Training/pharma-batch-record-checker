"""Hash chain (PHA-50). Synthetic rows only. No rules engine."""

import os
from datetime import datetime
from decimal import Decimal

import psycopg
import pytest

from pharma_batch_record_checker.handoff import FindingPayload
from pharma_batch_record_checker.schema import BatchRow, MasterRow
from pharma_batch_record_checker.store.hashchain import ChainFailure, verify_chain
from pharma_batch_record_checker.store.imports import insert_batch, insert_master
from pharma_batch_record_checker.store.review_store import PostgresReviewStore

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL is unset",
    ),
]

_WHEN = datetime(2026, 10, 12, 7, 0)
_VERIFIED = datetime(2026, 10, 12, 7, 5)


def _master() -> list[MasterRow]:
    """One synthetic master version with a limit that keeps a trailing zero."""
    return [
        MasterRow(
            master_record_id="MBR-CHAIN",
            master_version="1",
            product_name="Chain Check 10 mg tablets",
            batch_size=Decimal(1000),
            batch_size_unit="tablets",
            step_seq=1,
            step_id="S-1",
            unit_procedure="Dispensing",
            operation="Weigh",
            phase="Weigh API",
            parameter="Weight",
            unit="mg",
            min_value=Decimal("7.50"),
            max_value=Decimal("8.50"),
            critical=False,
        )
    ]


def _batch() -> list[BatchRow]:
    """The executed step for the chain master."""
    return [
        BatchRow(
            batch_id="B26-9001",
            master_record_id="MBR-CHAIN",
            master_version="1",
            step_id="S-1",
            reading=Decimal("9.25"),
            unit="mg",
            performed_by="OP-1",
            performed_at=_WHEN,
            verified_by="OP-2",
            verified_at=_VERIFIED,
            comment=None,
        )
    ]


def _finding() -> FindingPayload:
    """One out-of-range finding whose snapshot matches the stored step."""
    return FindingPayload(
        rule_id="out_of_range",
        step_severity="Needs Review",
        detail="Weight is above the maximum.",
        batch_id="B26-9001",
        master_record_id="MBR-CHAIN",
        master_version="1",
        step_id="S-1",
        step_seq=1,
        unit_procedure="Dispensing",
        operation="Weigh",
        phase="Weigh API",
        parameter="Weight",
        critical=False,
        expected_unit="mg",
        min_value=Decimal("7.50"),
        max_value=Decimal("8.50"),
        reading=Decimal("9.25"),
        recorded_unit="mg",
        performed_by="OP-1",
        performed_at=_WHEN,
        verified_by="OP-2",
        verified_at=_VERIFIED,
        comment=None,
    )


def _events(admin) -> list[dict]:
    return admin.execute(
        """
        SELECT event_seq, event_type, entity_id, prev_event_hash, event_hash
        FROM audit_event
        ORDER BY event_seq
        """
    ).fetchall()


def test_master_import_appends_the_genesis_event(admin):
    """The first event links to 64 zeros, and the app still cannot update the head."""
    insert_master(_master())

    assert verify_chain() is None
    events = _events(admin)
    assert len(events) == 1
    event = events[0]
    assert event["event_seq"] == 1
    assert event["event_type"] == "master_import"
    assert event["entity_id"] == '["MBR-CHAIN","1"]'
    assert event["prev_event_hash"] == "0" * 64
    head = admin.execute(
        "SELECT last_event_seq, last_event_hash FROM audit_head"
    ).fetchone()
    assert head["last_event_seq"] == 1
    assert head["last_event_hash"] == event["event_hash"]

    grants = {
        row["privilege_type"]
        for row in admin.execute(
            """
            SELECT privilege_type
            FROM information_schema.role_table_grants
            WHERE grantee = 'pharma_app'
              AND table_schema = 'public'
              AND table_name = 'audit_event'
            """
        ).fetchall()
    }
    assert grants == {"SELECT"}
    with psycopg.connect(os.environ["DATABASE_URL"]) as app:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute("UPDATE audit_head SET last_event_seq = 99")
        app.rollback()


def test_batch_and_review_extend_one_chain(admin):
    """A master, a batch, and a run are three linked events and the chain verifies."""
    insert_master(_master())
    insert_batch(_batch())
    run_id = PostgresReviewStore().insert_review_run(
        batch_id="B26-9001",
        rule_version="1.0.0",
        result_category="Needs Review",
        findings=(_finding(),),
    )

    assert verify_chain() is None
    events = _events(admin)
    assert [row["event_type"] for row in events] == [
        "master_import",
        "batch_import",
        "review_run",
    ]
    assert events[1]["prev_event_hash"] == events[0]["event_hash"]
    assert events[2]["prev_event_hash"] == events[1]["event_hash"]
    assert events[1]["entity_id"] == "B26-9001"
    assert events[2]["entity_id"] == run_id
    head = admin.execute(
        "SELECT last_event_seq, last_event_hash FROM audit_head"
    ).fetchone()
    assert head["last_event_seq"] == 3
    assert head["last_event_hash"] == events[2]["event_hash"]


def test_rolled_back_insert_writes_no_event(admin):
    """A duplicate step rolls the batch back and leaves the chain on the master."""
    insert_master(_master())
    step = _batch()[0]

    with pytest.raises(ValueError, match="duplicate step_id"):
        insert_batch([step, step])

    assert [row["event_type"] for row in _events(admin)] == ["master_import"]
    assert admin.execute("SELECT count(*) AS n FROM batch").fetchone()["n"] == 0
    assert (
        admin.execute("SELECT last_event_seq FROM audit_head").fetchone()[
            "last_event_seq"
        ]
        == 1
    )
    assert verify_chain() is None


def test_changed_master_row_breaks_the_payload_hash(admin):
    """An admin edit of a stored row is reported, and restoring it repairs the chain."""
    insert_master(_master())
    admin.execute(
        """
        UPDATE master_record
        SET product_name = 'Tampered 10 mg tablets'
        WHERE master_record_id = 'MBR-CHAIN' AND master_version = '1'
        """
    )
    admin.commit()

    assert verify_chain() == ChainFailure(1, "payload_hash")

    admin.execute(
        """
        UPDATE master_record
        SET product_name = 'Chain Check 10 mg tablets'
        WHERE master_record_id = 'MBR-CHAIN' AND master_version = '1'
        """
    )
    admin.commit()
    assert verify_chain() is None


def test_changed_event_hash_is_reported(admin):
    """A rewritten event hash is reported on that event, not as a payload miss."""
    insert_master(_master())
    insert_batch(_batch())
    admin.execute(
        """
        UPDATE audit_event
        SET event_hash = repeat('ab', 32)
        WHERE event_seq = 2
        """
    )
    admin.commit()

    assert verify_chain() == ChainFailure(2, "event_hash")
