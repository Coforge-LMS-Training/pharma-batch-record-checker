"""Persist one review run. This is the store behind handoff.ReviewStore.

The store does not evaluate rules. It writes the payload it is given, or it
rolls the run back.
"""

import uuid

import psycopg

from pharma_batch_record_checker.handoff import PAYLOAD_FIELDS, FindingPayload
from pharma_batch_record_checker.review import ResultCategory
from pharma_batch_record_checker.rules import RULE_IDS
from pharma_batch_record_checker.store.connection import transaction
from pharma_batch_record_checker.store.errors import BatchNotFound, InvalidReview

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
        run_id = uuid.uuid4()
        try:
            with transaction() as conn:
                if not _batch_exists(conn, batch_id):
                    raise BatchNotFound(f"no batch {batch_id}")
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
        except psycopg.errors.CheckViolation as exc:
            raise InvalidReview("review run was rejected") from exc
        return str(run_id)


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
