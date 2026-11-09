"""Insert one master version or one executed batch.

Rows have already been accepted by schema.MasterRow or schema.BatchRow.
A failure rolls the whole insert back, including its hash-chain event. A
second insert of the same master version or batch id raises DuplicateRecord
and leaves the first rows in place.
"""

from collections.abc import Sequence

import psycopg

from pharma_batch_record_checker.schema import BatchRow, MasterRow
from pharma_batch_record_checker.store.connection import transaction
from pharma_batch_record_checker.store.errors import DuplicateRecord, MasterNotFound
from pharma_batch_record_checker.store.hashchain import (
    BATCH_IMPORT,
    MASTER_IMPORT,
    append_event,
    master_entity_id,
)


def insert_master(rows: Sequence[MasterRow]) -> None:
    """Store one master version and its steps.

    Every row must share one master id, version, product, batch size, and unit.
    """
    header = _one_master(rows)
    with transaction() as conn:
        if _master_exists(conn, header.master_record_id, header.master_version):
            raise DuplicateRecord(
                f"master {header.master_record_id} v{header.master_version} "
                "already exists"
            )
        try:
            conn.execute(
                """
                INSERT INTO master_record (
                    master_record_id, master_version, product_name,
                    batch_size, batch_size_unit
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    header.master_record_id,
                    header.master_version,
                    header.product_name,
                    header.batch_size,
                    header.batch_size_unit,
                ),
            )
        except psycopg.errors.UniqueViolation as exc:
            raise DuplicateRecord(
                f"master {header.master_record_id} v{header.master_version} "
                "already exists"
            ) from exc
        for row in rows:
            try:
                conn.execute(
                    """
                    INSERT INTO master_step (
                        master_record_id, master_version, step_seq, step_id,
                        unit_procedure, operation, phase, parameter, unit,
                        min_value, max_value, critical
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        row.master_record_id,
                        row.master_version,
                        row.step_seq,
                        row.step_id,
                        row.unit_procedure,
                        row.operation,
                        row.phase,
                        row.parameter,
                        row.unit,
                        row.min_value,
                        row.max_value,
                        row.critical,
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise ValueError(
                    f"master {header.master_record_id} v{header.master_version}: "
                    f"duplicate step_id or step_seq {row.step_id!r}"
                ) from exc
        append_event(
            conn,
            MASTER_IMPORT,
            master_entity_id(header.master_record_id, header.master_version),
        )


def insert_batch(rows: Sequence[BatchRow]) -> None:
    """Store one executed batch and its steps.

    The master version must already exist. Readings, units, names, and
    comments are stored as given.
    """
    header = _one_batch(rows)
    with transaction() as conn:
        if not _master_exists(conn, header.master_record_id, header.master_version):
            raise MasterNotFound(
                f"no master {header.master_record_id} v{header.master_version}"
            )
        if _batch_exists(conn, header.batch_id):
            raise DuplicateRecord(f"batch {header.batch_id} already exists")
        try:
            conn.execute(
                """
                INSERT INTO batch (batch_id, master_record_id, master_version)
                VALUES (%s, %s, %s)
                """,
                (header.batch_id, header.master_record_id, header.master_version),
            )
        except psycopg.errors.UniqueViolation as exc:
            raise DuplicateRecord(f"batch {header.batch_id} already exists") from exc
        for row in rows:
            try:
                conn.execute(
                    """
                    INSERT INTO batch_step (
                        batch_id, step_id, reading, unit, performed_by,
                        performed_at, verified_by, verified_at, comment
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        row.batch_id,
                        row.step_id,
                        row.reading,
                        row.unit,
                        row.performed_by,
                        row.performed_at,
                        row.verified_by,
                        row.verified_at,
                        row.comment,
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise ValueError(
                    f"batch {header.batch_id}: duplicate step_id {row.step_id!r}"
                ) from exc
        append_event(conn, BATCH_IMPORT, header.batch_id)


def _one_master(rows: Sequence[MasterRow]) -> MasterRow:
    """The shared header, or ValueError if the rows are not one version."""
    if not rows:
        raise ValueError("a master version needs at least one step row")
    header = rows[0]
    for row in rows[1:]:
        same = (
            row.master_record_id == header.master_record_id
            and row.master_version == header.master_version
            and row.product_name == header.product_name
            and row.batch_size == header.batch_size
            and row.batch_size_unit == header.batch_size_unit
        )
        if not same:
            raise ValueError("all rows must be one master version with the same header")
    return header


def _one_batch(rows: Sequence[BatchRow]) -> BatchRow:
    """The shared batch header, or ValueError if the rows are not one batch."""
    if not rows:
        raise ValueError("a batch needs at least one step row")
    header = rows[0]
    for row in rows[1:]:
        if row.batch_id != header.batch_id:
            raise ValueError("all rows must belong to one batch")
        if (
            row.master_record_id != header.master_record_id
            or row.master_version != header.master_version
        ):
            raise ValueError(
                f"batch {header.batch_id} names more than one master version"
            )
    return header


def _master_exists(conn: psycopg.Connection, master_id: str, version: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM master_record
        WHERE master_record_id = %s AND master_version = %s
        """,
        (master_id, version),
    ).fetchone()
    return row is not None


def _batch_exists(conn: psycopg.Connection, batch_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM batch WHERE batch_id = %s",
        (batch_id,),
    ).fetchone()
    return row is not None
