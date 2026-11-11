"""Persist one review run. This is the store behind handoff.ReviewStore.

The store does not evaluate rules. It writes the payload it is given, or it
rolls the run back. The run and its hash-chain event commit together.
"""

import uuid

import psycopg

from pharma_batch_record_checker.handoff import PAYLOAD_FIELDS, FindingPayload
from pharma_batch_record_checker.review import ResultCategory
from pharma_batch_record_checker.rules import RULE_IDS
from pharma_batch_record_checker.store.connection import transaction
from pharma_batch_record_checker.store.errors import (
    BatchNotFound,
    InvalidReview,
    StaleReview,
)
from pharma_batch_record_checker.store.hashchain import REVIEW_RUN, append_event

_RULE_IDS = frozenset(RULE_IDS)
_SEVERITIES = frozenset({"Critical", "Needs Review"})
_CATEGORIES = frozenset({"Critical", "Needs Review", "No Findings"})
_FINDING_COLUMNS = ("finding_id", "run_id", "finding_seq", *PAYLOAD_FIELDS)


class PostgresReviewStore:
    """Inserts a review run and its findings for handoff.hand_off."""

    def insert_review_run(
        self,
        batch_id: str,
        rule_version: str,
        result_category: ResultCategory,
        findings: tuple[FindingPayload, ...],
    ) -> str:
        """Store one batch's run. Returns the new run id.

        The batch must already exist. An unknown rule, severity, or result
        category raises InvalidReview and leaves no run and no findings.
        """
        return _store_run(batch_id, rule_version, result_category, findings)

    def insert_review_run_after(
        self,
        batch_id: str,
        rule_version: str,
        result_category: ResultCategory,
        findings: tuple[FindingPayload, ...],
        *,
        expected_latest_run_id: str | None,
    ) -> str:
        """Store a run only if the batch's latest run is still the expected one.

        expected_latest_run_id is the run the caller reviewed from, or None
        for a batch with no run. If another run is now the latest, StaleReview
        is raised and nothing is stored. This is how a re-review stays a
        single run under double clicks and parallel requests. Otherwise it
        behaves exactly like insert_review_run.
        """
        return _store_run(
            batch_id,
            rule_version,
            result_category,
            findings,
            check_latest=True,
            expected_latest_run_id=expected_latest_run_id,
        )


def _store_run(
    batch_id: str,
    rule_version: str,
    result_category: ResultCategory,
    findings: tuple[FindingPayload, ...],
    *,
    check_latest: bool = False,
    expected_latest_run_id: str | None = None,
) -> str:
    """Insert one run, its findings and its chain event in one transaction.

    Every run insert first takes a transaction-level advisory lock on the
    batch, so runs for one batch are written one at a time and the latest-run
    check cannot race. The lock is released at commit or rollback.
    """
    run_id = uuid.uuid4()
    try:
        with transaction() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"review_run:{batch_id}",),
            )
            if not _batch_exists(conn, batch_id):
                raise BatchNotFound(f"no batch {batch_id}")
            if check_latest:
                latest = _latest_run_id(conn, batch_id)
                if latest != expected_latest_run_id:
                    raise StaleReview(
                        f"batch {batch_id} has a newer run than "
                        f"{expected_latest_run_id or 'none'}"
                    )
            if result_category not in _CATEGORIES:
                raise InvalidReview(f"unknown result_category {result_category!r}")
            conn.execute(
                """
                INSERT INTO review_run (
                    run_id, batch_id, rule_version, result_category
                ) VALUES (%s, %s, %s, %s)
                """,
                (run_id, batch_id, rule_version, result_category),
            )
            for seq, finding in enumerate(findings):
                _check_finding(finding, batch_id)
                _insert_finding(conn, uuid.uuid4(), run_id, seq, finding)
            append_event(conn, REVIEW_RUN, str(run_id))
    except psycopg.errors.CheckViolation as exc:
        raise InvalidReview("review run was rejected") from exc
    return str(run_id)


def _latest_run_id(conn: psycopg.Connection, batch_id: str) -> str | None:
    """The batch's latest run id, ordered as the pages order runs."""
    row = conn.execute(
        """
        SELECT run_id FROM review_run
        WHERE batch_id = %s
        ORDER BY created_at DESC, run_id DESC
        LIMIT 1
        """,
        (batch_id,),
    ).fetchone()
    return None if row is None else str(row[0])


def _batch_exists(conn: psycopg.Connection, batch_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM batch WHERE batch_id = %s",
        (batch_id,),
    ).fetchone()
    return row is not None


def _check_finding(finding: FindingPayload, batch_id: str) -> None:
    """Reject a finding the review tables would not keep."""
    if finding.rule_id not in _RULE_IDS:
        raise InvalidReview(f"unknown rule_id {finding.rule_id!r}")
    if finding.step_severity not in _SEVERITIES:
        raise InvalidReview(f"unknown step_severity {finding.step_severity!r}")
    if finding.batch_id != batch_id:
        raise InvalidReview(
            f"finding batch_id {finding.batch_id!r} does not match {batch_id!r}"
        )


def _insert_finding(
    conn: psycopg.Connection,
    finding_id: uuid.UUID,
    run_id: uuid.UUID,
    seq: int,
    finding: FindingPayload,
) -> None:
    values = (
        finding_id,
        run_id,
        seq,
        *(getattr(finding, name) for name in PAYLOAD_FIELDS),
    )
    columns = ", ".join(_FINDING_COLUMNS)
    placeholders = ", ".join(["%s"] * len(values))
    conn.execute(
        f"INSERT INTO finding ({columns}) VALUES ({placeholders})",
        values,
    )
