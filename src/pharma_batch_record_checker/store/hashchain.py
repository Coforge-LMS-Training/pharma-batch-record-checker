"""SHA-256 chain over masters, batches, and review runs.

Each insert writes one audit_event in the same transaction as its rows.
reserve_audit_slot and append_audit_event are owned by pharma_admin, so
the head moves even though pharma_app has no UPDATE on audit_head.

Canonical JSON is UTF-8 with keys sorted and separators "," and ":".
None is null. A bool is true or false. A Decimal is format(value, "f"),
so 18.0 and 18.00 hash differently. A CSV timestamp is YYYY-MM-DDTHH:MM.
A store timestamp is UTC with microseconds and a Z suffix, taken from the
value stored on the row.

payload_hash is the SHA-256 hex of that JSON. event_hash is the SHA-256
hex of a second object: prev_event_hash, event_seq, event_type, entity_id,
payload_hash, and created_at. Hashes are lowercase hex.

A master payload is the header plus steps in step_seq order. Its entity id
is a JSON array of master_record_id and master_version. A batch payload is
the header plus steps in step_id order, using the C collation. A review
payload is the run plus findings in finding_seq order. The entity id of a
batch is its batch_id, and the entity id of a run is its run_id.

verify_chain() walks event_seq order and returns the first event whose
previous-hash link, event hash, or payload hash does not recompute. An
empty chain is intact. Someone who rewrites an event and every following
hash can still lie. The check is there to catch a partial edit.
"""

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from psycopg import Connection

from pharma_batch_record_checker.handoff import PAYLOAD_FIELDS
from pharma_batch_record_checker.schema import TIMESTAMP_FORMAT
from pharma_batch_record_checker.store.connection import transaction

MASTER_IMPORT = "master_import"
BATCH_IMPORT = "batch_import"
REVIEW_RUN = "review_run"
FINDING_NOTE = "finding_note"

_EVENT_TYPES = frozenset({MASTER_IMPORT, BATCH_IMPORT, REVIEW_RUN, FINDING_NOTE})
_GENESIS = "0" * 64

_MASTER_HEADER = (
    "master_record_id",
    "master_version",
    "product_name",
    "batch_size",
    "batch_size_unit",
)
_MASTER_STEP = (
    "step_seq",
    "step_id",
    "unit_procedure",
    "operation",
    "phase",
    "parameter",
    "unit",
    "min_value",
    "max_value",
    "critical",
)
_BATCH_HEADER = ("batch_id", "master_record_id", "master_version")
_BATCH_STEP = (
    "step_id",
    "reading",
    "unit",
    "performed_by",
    "performed_at",
    "verified_by",
    "verified_at",
    "comment",
)
_RUN = ("run_id", "batch_id", "rule_version", "result_category", "created_at")
_FINDING = ("finding_id", "run_id", "finding_seq", *PAYLOAD_FIELDS)


@dataclass(frozen=True)
class ChainFailure:
    """The first event verify_chain could not accept.

    check is prev_event_hash, event_hash, or payload_hash, in that order.
    """

    event_seq: int
    check: str


def master_entity_id(master_record_id: str, master_version: str) -> str:
    """Entity id for one master version. The pair is a JSON array."""
    return canonical_json([master_record_id, master_version])


def append_event(conn: Connection, event_type: str, entity_id: str) -> None:
    """Append one event for rows already written on this transaction.

    The head lock is held until the transaction ends. A failure here rolls
    the data rows back with the event.
    """
    if event_type not in _EVENT_TYPES:
        raise ValueError(f"unknown event_type {event_type!r}")
    payload = _payload(conn, event_type, entity_id)
    if payload is None:
        raise RuntimeError(f"no stored rows for {event_type} {entity_id}")
    payload_hash = _digest(canonical_json(payload))
    slot = conn.execute(
        "SELECT event_seq, prev_event_hash, created_at FROM reserve_audit_slot()"
    ).fetchone()
    if slot is None:
        raise RuntimeError("audit head is missing")
    event_seq = int(slot[0])
    prev_event_hash = str(slot[1])
    created_at = slot[2]
    if not isinstance(created_at, datetime):
        raise TypeError("audit event time was not a timestamp")
    event_hash = _digest(
        canonical_json(
            _event_body(
                prev_event_hash,
                event_seq,
                event_type,
                entity_id,
                payload_hash,
                format_store_timestamp(created_at),
            )
        )
    )
    conn.execute(
        "SELECT append_audit_event(%s, %s, %s, %s, %s, %s, %s)",
        (
            event_seq,
            event_type,
            entity_id,
            payload_hash,
            prev_event_hash,
            event_hash,
            created_at,
        ),
    )


def verify_chain() -> ChainFailure | None:
    """Return the first broken event, or None when the chain still matches."""
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT event_seq, event_type, entity_id, payload_hash,
                   prev_event_hash, event_hash, created_at
            FROM audit_event
            ORDER BY event_seq
            """
        ).fetchall()
        previous = _GENESIS
        for row in rows:
            event_seq = int(row[0])
            event_type = str(row[1])
            entity_id = str(row[2])
            payload_hash = str(row[3])
            prev_event_hash = str(row[4])
            event_hash = str(row[5])
            created_at = row[6]
            if prev_event_hash != previous:
                return ChainFailure(event_seq, "prev_event_hash")
            if not isinstance(created_at, datetime):
                return ChainFailure(event_seq, "event_hash")
            recomputed = _digest(
                canonical_json(
                    _event_body(
                        prev_event_hash,
                        event_seq,
                        event_type,
                        entity_id,
                        payload_hash,
                        format_store_timestamp(created_at),
                    )
                )
            )
            if recomputed != event_hash:
                return ChainFailure(event_seq, "event_hash")
            current = _payload(conn, event_type, entity_id)
            if _digest(canonical_json(current)) != payload_hash:
                return ChainFailure(event_seq, "payload_hash")
            previous = event_hash
    return None


def canonical_json(value: object) -> str:
    """UTF-8 JSON with sorted keys and no extra whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def format_store_timestamp(value: datetime) -> str:
    """UTC time with microseconds, from the timestamp stored on the row."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _event_body(
    prev_event_hash: str,
    event_seq: int,
    event_type: str,
    entity_id: str,
    payload_hash: str,
    created_at: str,
) -> dict[str, object]:
    return {
        "prev_event_hash": prev_event_hash,
        "event_seq": event_seq,
        "event_type": event_type,
        "entity_id": entity_id,
        "payload_hash": payload_hash,
        "created_at": created_at,
    }


def _payload(conn: Connection, event_type: str, entity_id: str) -> object | None:
    if event_type == MASTER_IMPORT:
        return _master_payload(conn, entity_id)
    if event_type == BATCH_IMPORT:
        return _batch_payload(conn, entity_id)
    if event_type == REVIEW_RUN:
        return _review_payload(conn, entity_id)
    return None


def _master_payload(conn: Connection, entity_id: str) -> dict[str, object] | None:
    key = _master_key(entity_id)
    if key is None:
        return None
    header = conn.execute(
        f"""
        SELECT {", ".join(_MASTER_HEADER)}
        FROM master_record
        WHERE master_record_id = %s AND master_version = %s
        """,
        key,
    ).fetchone()
    if header is None:
        return None
    steps = conn.execute(
        f"""
        SELECT {", ".join(_MASTER_STEP)}
        FROM master_step
        WHERE master_record_id = %s AND master_version = %s
        ORDER BY step_seq
        """,
        key,
    ).fetchall()
    payload = _record(_MASTER_HEADER, header)
    payload["steps"] = [_record(_MASTER_STEP, step) for step in steps]
    return payload


def _batch_payload(conn: Connection, entity_id: str) -> dict[str, object] | None:
    header = conn.execute(
        f"""
        SELECT {", ".join(_BATCH_HEADER)}
        FROM batch
        WHERE batch_id = %s
        """,
        (entity_id,),
    ).fetchone()
    if header is None:
        return None
    steps = conn.execute(
        f"""
        SELECT {", ".join(_BATCH_STEP)}
        FROM batch_step
        WHERE batch_id = %s
        ORDER BY step_id COLLATE "C"
        """,
        (entity_id,),
    ).fetchall()
    payload = _record(_BATCH_HEADER, header)
    payload["steps"] = [_record(_BATCH_STEP, step) for step in steps]
    return payload


def _review_payload(conn: Connection, entity_id: str) -> dict[str, object] | None:
    header = conn.execute(
        f"""
        SELECT {", ".join(_RUN)}
        FROM review_run
        WHERE run_id = %s
        """,
        (entity_id,),
    ).fetchone()
    if header is None:
        return None
    findings = conn.execute(
        f"""
        SELECT {", ".join(_FINDING)}
        FROM finding
        WHERE run_id = %s
        ORDER BY finding_seq
        """,
        (entity_id,),
    ).fetchall()
    payload = _record(_RUN, header)
    payload["findings"] = [_record(_FINDING, finding) for finding in findings]
    return payload


def _master_key(entity_id: str) -> tuple[str, str] | None:
    try:
        parsed = json.loads(entity_id)
    except json.JSONDecodeError:
        return None
    if (
        isinstance(parsed, list)
        and len(parsed) == 2
        and isinstance(parsed[0], str)
        and isinstance(parsed[1], str)
    ):
        return parsed[0], parsed[1]
    return None


def _record(columns: Sequence[str], row: Sequence[object]) -> dict[str, object]:
    return {
        column: _json_value(value) for column, value in zip(columns, row, strict=True)
    }


def _json_value(value: object) -> object:
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.strftime(TIMESTAMP_FORMAT)
        return format_store_timestamp(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"cannot hash a {type(value).__name__}")
