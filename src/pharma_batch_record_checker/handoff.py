"""Hand reviewed batches to the store (PHA-67): payload shape and the call.

The rules engine builds the payload; the store (owner: Ansh) persists it. The
agreed store function is

    insert_review_run(batch_id, rule_version, result_category, findings)

called once per reviewed batch. This module only builds the data and makes
that call: there is no database, hash or UI code here, and no hash or run
timestamp is passed (the store adds those). It imports nothing from the store.

- Each finding carries its rule code (lower case, as in CLAUDE.md), the step's
  severity, the reviewer-readable detail, and a full snapshot of the master
  step and the batch row. For missing_step the batch-side fields are None.
- The operator comment is copied as data only; nothing here reads it.
- Order is deterministic: batches in file order; findings by step_seq, then
  rule_id (from review.py).
- A batch blocked by parse or matching errors has no result_category and is
  never inserted, so it can never be stored as No Findings. Its errors stay in
  ReviewRun.errors.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from pharma_batch_record_checker.review import (
    BatchReview,
    ResultCategory,
    ReviewFinding,
    ReviewRun,
    Severity,
)
from pharma_batch_record_checker.rules import RuleId

# The finding fields agreed with the store (PHA-43), in this order.
PAYLOAD_FIELDS: tuple[str, ...] = (
    # Finding
    "rule_id",
    "step_severity",
    "detail",
    # Identity
    "batch_id",
    "master_record_id",
    "master_version",
    "step_id",
    # Master snapshot
    "step_seq",
    "unit_procedure",
    "operation",
    "phase",
    "parameter",
    "critical",
    "expected_unit",
    "min_value",
    "max_value",
    # Batch snapshot (all None for missing_step)
    "reading",
    "recorded_unit",
    "performed_by",
    "performed_at",
    "verified_by",
    "verified_at",
    "comment",
)

# Engine field name -> agreed payload name. Every other field keeps its name.
_RENAMED: Mapping[str, str] = MappingProxyType(
    {"master_unit": "expected_unit", "batch_unit": "recorded_unit"}
)


@dataclass(frozen=True, kw_only=True)
class FindingPayload:
    """One finding as handed to the store. Values keep their parsed types."""

    rule_id: RuleId
    step_severity: Severity
    detail: str
    batch_id: str
    master_record_id: str
    master_version: str
    step_id: str
    step_seq: int
    unit_procedure: str
    operation: str
    phase: str
    parameter: str | None
    critical: bool
    expected_unit: str | None
    min_value: Decimal | None
    max_value: Decimal | None
    reading: Decimal | None
    recorded_unit: str | None
    performed_by: str | None
    performed_at: datetime | None
    verified_by: str | None
    verified_at: datetime | None
    comment: str | None


if tuple(f.name for f in fields(FindingPayload)) != PAYLOAD_FIELDS:
    raise RuntimeError("FindingPayload fields drifted from PAYLOAD_FIELDS")


@runtime_checkable
class ReviewStore(Protocol):
    """The store function this module calls (implemented by the store owner)."""

    def insert_review_run(
        self,
        batch_id: str,
        rule_version: str,
        result_category: ResultCategory,
        findings: tuple[FindingPayload, ...],
    ) -> object:
        """Persist one batch's review run and its findings."""
        ...


def to_payload(finding: ReviewFinding) -> FindingPayload:
    """Map one reviewed finding to the agreed payload, renaming the units.

    Raises TypeError if the engine's finding gains or loses a field the
    payload doesn't map, so the contract can't drift silently.
    """
    values = {
        _RENAMED.get(f.name, f.name): getattr(finding, f.name)
        for f in fields(ReviewFinding)
    }
    return FindingPayload(**values)


def build_payload(batch: BatchReview) -> tuple[FindingPayload, ...]:
    """A reviewed batch's findings as payloads, in review order."""
    return tuple(to_payload(f) for f in batch.findings)


def hand_off(run: ReviewRun, store: ReviewStore) -> Mapping[str, object]:
    """Insert each reviewed batch of the run, in file order.

    Blocked batches are not in run.batches and are never inserted. Returns
    what the store returned for each inserted batch_id (e.g. its run ID).
    """
    results = {
        batch_id: store.insert_review_run(
            batch_id=batch_id,
            rule_version=run.rule_version,
            result_category=batch.result_category,
            findings=build_payload(batch),
        )
        for batch_id, batch in run.batches.items()
    }
    return MappingProxyType(results)
