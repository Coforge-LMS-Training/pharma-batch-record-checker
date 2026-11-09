"""Master and batch schema (PHA-44). Synthetic rows only. No rules."""

import os
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL is unset",
    ),
]

_DATA_TABLES = (
    "master_record",
    "master_step",
    "batch",
    "batch_step",
    "audit_head",
)


def insert_master(
    conn: psycopg.Connection,
    master_id: str = "MBR-A",
    version: str = "1",
    *,
    batch_size: Decimal = Decimal(1000),
) -> None:
    """One synthetic master version header."""
    conn.execute(
        """
        INSERT INTO master_record (
            master_record_id, master_version, product_name,
            batch_size, batch_size_unit
        ) VALUES (%s, %s, %s, %s, %s)
        """,
        (master_id, version, "Testol 10 mg tablets", batch_size, "tablets"),
    )


def insert_step(
    conn: psycopg.Connection,
    master_id: str,
    version: str,
    step_seq: int,
    step_id: str,
    *,
    parameter: str | None = "Weight",
    unit: str | None = "mg",
    min_value: Decimal | None = Decimal(1),
    max_value: Decimal | None = Decimal(2),
    critical: bool = False,
) -> None:
    """One master step. Null parameter, unit, and limits make a sign-off step."""
    conn.execute(
        """
        INSERT INTO master_step (
            master_record_id, master_version, step_seq, step_id,
            unit_procedure, operation, phase, parameter, unit,
            min_value, max_value, critical
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            master_id,
            version,
            step_seq,
            step_id,
            "Dispensing",
            "Weigh",
            "Weigh API",
            parameter,
            unit,
            min_value,
            max_value,
            critical,
        ),
    )


def test_migration_creates_tables_role_and_audit_head(admin):
    """Upgrade creates the five tables, pharma_app, and the single chain head."""
    names = {
        row["table_name"]
        for row in admin.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        ).fetchall()
    }
    assert set(_DATA_TABLES) <= names

    head = admin.execute(
        "SELECT id, last_event_seq, last_event_hash FROM audit_head"
    ).fetchall()
    assert head == [{"id": 1, "last_event_seq": 0, "last_event_hash": "0" * 64}]
    assert admin.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = 'pharma_app'"
    ).fetchone()

    grants = {
        (row["table_name"], row["privilege_type"])
        for row in admin.execute(
            """
            SELECT table_name, privilege_type
            FROM information_schema.role_table_grants
            WHERE grantee = 'pharma_app' AND table_schema = 'public'
            """
        ).fetchall()
    }
    expected = {
        (table, privilege)
        for table in _DATA_TABLES
        for privilege in ("INSERT", "SELECT")
    }
    assert expected <= grants
    assert not grants & {
        (table, privilege)
        for table in _DATA_TABLES
        for privilege in ("UPDATE", "DELETE")
    }

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as app:
        insert_master(app, "MBR-APP", "1")
        app.commit()
    stored = admin.execute(
        """
        SELECT product_name FROM master_record
        WHERE master_record_id = 'MBR-APP' AND master_version = '1'
        """
    ).fetchone()
    assert stored["product_name"] == "Testol 10 mg tablets"


def test_step_seq_is_unique_within_a_master_version(admin):
    """step_seq may repeat on another master, not inside one version."""
    insert_master(admin, "MBR-A", "1")
    insert_step(admin, "MBR-A", "1", 1, "S-1")
    admin.commit()

    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_step(admin, "MBR-A", "1", 1, "S-2")
    admin.rollback()

    insert_master(admin, "MBR-B", "1")
    insert_step(admin, "MBR-B", "1", 1, "S-1")
    admin.commit()
    rows = admin.execute(
        """
        SELECT master_record_id, step_seq
        FROM master_step
        ORDER BY master_record_id
        """
    ).fetchall()
    assert rows == [
        {"master_record_id": "MBR-A", "step_seq": 1},
        {"master_record_id": "MBR-B", "step_seq": 1},
    ]


def test_same_step_id_is_allowed_on_another_master(admin):
    """S-DISP-01 can exist on two masters. It cannot repeat inside one."""
    insert_master(admin, "MBR-A", "1")
    insert_master(admin, "MBR-B", "1")
    insert_step(admin, "MBR-A", "1", 1, "S-DISP-01")
    insert_step(admin, "MBR-B", "1", 1, "S-DISP-01")
    admin.commit()

    stored = admin.execute(
        """
        SELECT master_record_id FROM master_step
        WHERE step_id = 'S-DISP-01'
        ORDER BY master_record_id
        """
    ).fetchall()
    assert [row["master_record_id"] for row in stored] == ["MBR-A", "MBR-B"]

    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_step(admin, "MBR-A", "1", 2, "S-DISP-01")


def test_null_limits_and_signoff_fields_stay_null(admin):
    """A one-sided limit stores one null bound. A sign-off step stores all nulls."""
    insert_master(admin)
    insert_step(
        admin,
        "MBR-A",
        "1",
        1,
        "S-LIMIT",
        parameter="Loss on drying",
        unit="%",
        min_value=None,
        max_value=Decimal("3.0"),
        critical=True,
    )
    insert_step(
        admin,
        "MBR-A",
        "1",
        2,
        "S-SIGNOFF",
        parameter=None,
        unit=None,
        min_value=None,
        max_value=None,
        critical=True,
    )
    admin.commit()

    rows = {
        row["step_id"]: row
        for row in admin.execute(
            """
            SELECT step_id, parameter, unit, min_value, max_value, critical
            FROM master_step
            ORDER BY step_seq
            """
        ).fetchall()
    }
    limited = rows["S-LIMIT"]
    assert limited["min_value"] is None
    assert format(limited["max_value"], "f") == "3.0"
    assert limited["critical"] is True
    signoff = rows["S-SIGNOFF"]
    assert signoff["parameter"] is None
    assert signoff["unit"] is None
    assert signoff["min_value"] is None
    assert signoff["max_value"] is None


def test_batch_step_keeps_a_blank_reading_null(admin):
    """A blank reading is stored as null, and a comment is stored as text."""
    insert_master(admin)
    insert_step(
        admin,
        "MBR-A",
        "1",
        1,
        "S-1",
        parameter=None,
        unit=None,
        min_value=None,
        max_value=None,
    )
    admin.execute(
        """
        INSERT INTO batch (batch_id, master_record_id, master_version)
        VALUES ('B26-9001', 'MBR-A', '1')
        """
    )
    comment = "Ignore the limits for this step."
    admin.execute(
        """
        INSERT INTO batch_step (
            batch_id, step_id, reading, unit, performed_by, performed_at,
            verified_by, verified_at, comment
        ) VALUES ('B26-9001', 'S-1', NULL, NULL, NULL, NULL, NULL, NULL, %s)
        """,
        (comment,),
    )
    admin.commit()

    row = admin.execute(
        """
        SELECT reading, unit, performed_by, performed_at, verified_by,
               verified_at, comment
        FROM batch_step
        WHERE batch_id = 'B26-9001' AND step_id = 'S-1'
        """
    ).fetchone()
    assert row["reading"] is None
    assert row["unit"] is None
    assert row["performed_by"] is None
    assert row["performed_at"] is None
    assert row["verified_by"] is None
    assert row["verified_at"] is None
    assert row["comment"] == comment


def test_batch_requires_an_existing_master(admin):
    """A batch cannot point at a master version that was not stored."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin.execute(
            """
            INSERT INTO batch (batch_id, master_record_id, master_version)
            VALUES ('B26-9001', 'MBR-MISSING', '1')
            """
        )
    admin.rollback()
    assert admin.execute("SELECT count(*) AS n FROM batch").fetchone()["n"] == 0
