"""Re-review stored batches with the running rules. Reviews only move forward.

A batch is reviewed when it is first loaded. After a rules upgrade the
stored runs still carry the old rule_version, so this module rebuilds each
stored batch from its stored rows, reviews it with the running engine, and
appends a new run stamped with RULE_VERSION. Nothing is edited or deleted:
the earlier runs, their findings and the hash chain stay as they were.

Forward only. A batch is re-reviewed only when its latest run used an older
rule_version than the running one, or when it has no run. If the latest run
is from the same rules, nothing happens; if it is from newer rules (older
code running on a newer database) or its version can't be ordered, the batch
is left alone and reported. The run is stored with
insert_review_run_after, so two clicks or two requests add one run.
"""

import logging
import re
from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError

from pharma_batch_record_checker import rules
from pharma_batch_record_checker.handoff import build_payload
from pharma_batch_record_checker.matching import MatchedBatch, match_batches
from pharma_batch_record_checker.parser import ParseResult
from pharma_batch_record_checker.records import BatchStep, MasterVersion
from pharma_batch_record_checker.review import ResultCategory, review_batch
from pharma_batch_record_checker.schema import BatchRow, MasterRow
from pharma_batch_record_checker.store.errors import StaleReview, StoreError
from pharma_batch_record_checker.store.reads import (
    StoredBatch,
    StoredMaster,
    StoredRun,
    get_batch,
    get_master,
    latest_runs,
    list_runs,
)
from pharma_batch_record_checker.store.review_store import PostgresReviewStore

logger = logging.getLogger(__name__)

_VERSION = re.compile(r"\d+(\.\d+)*")
_KEY_LENGTH = 3


class RebuildError(StoreError):
    """The stored rows could not be rebuilt into a reviewable batch."""


class Standing(StrEnum):
    """Where a batch's latest run stands against the running rules."""

    NOT_REVIEWED = "not-reviewed"
    OLDER = "older"
    CURRENT = "current"
    NEWER = "newer"
    UNKNOWN = "unknown"

    @property
    def moves_forward(self) -> bool:
        """True when a new run with the running rules may be appended."""
        return self in {Standing.NOT_REVIEWED, Standing.OLDER}


@dataclass(frozen=True)
class RereviewResult:
    """What a re-review of one batch did.

    standing is the batch's standing before the call. run_id and
    result_category are set only when a run was appended. A StaleReview
    (someone else appended first) is reported as CURRENT with no run.
    """

    batch_id: str
    standing: Standing
    latest_version: str | None
    run_id: str | None = None
    result_category: ResultCategory | None = None


@dataclass(frozen=True)
class SyncReport:
    """What "Re-review all" did, by batch id in batch_id order."""

    rereviewed: tuple[str, ...] = ()
    current: tuple[str, ...] = ()
    newer: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()


def running_version() -> str:
    """The rule_version the running engine stamps on a run."""
    return rules.RULE_VERSION


def version_key(version: str) -> tuple[int, ...] | None:
    """Dotted numbers as a comparable tuple, padded to three parts.

    "1.0" and "1.0.0" are equal and "1.10.0" is newer than "1.9.0". Anything
    other than plain dotted digits (a prefix, a suffix, spaces) is None.
    """
    if not _VERSION.fullmatch(version):
        return None
    parts = tuple(int(part) for part in version.split("."))
    return parts + (0,) * (_KEY_LENGTH - len(parts))


def standing(latest_version: str | None, current: str) -> Standing:
    """The standing of a batch whose latest run used latest_version."""
    if latest_version is None:
        return (
            Standing.NOT_REVIEWED
            if version_key(current) is not None
            else Standing.UNKNOWN
        )
    latest_key = version_key(latest_version)
    current_key = version_key(current)
    if latest_key is None or current_key is None:
        return Standing.UNKNOWN
    width = max(len(latest_key), len(current_key))
    latest_key += (0,) * (width - len(latest_key))
    current_key += (0,) * (width - len(current_key))
    if latest_key < current_key:
        return Standing.OLDER
    if latest_key > current_key:
        return Standing.NEWER
    return Standing.CURRENT


def rebuild(master: StoredMaster, batch: StoredBatch) -> MatchedBatch:
    """Turn stored master and batch rows back into the batch the engine reviews.

    The rows go through the same schema models, record types and matcher as
    a CSV load, so a stored batch is reviewed exactly as a loaded one. Any
    row the models refuse, or any matching error, raises RebuildError.
    """
    try:
        version = MasterVersion.from_rows(
            MasterRow(
                master_record_id=master.master_record_id,
                master_version=master.master_version,
                product_name=master.product_name,
                batch_size=master.batch_size,
                batch_size_unit=master.batch_size_unit,
                **{name: getattr(step, name) for name in _MASTER_STEP_FIELDS},
            )
            for step in master.steps
        )
        steps = tuple(
            BatchStep.from_row(
                BatchRow(
                    batch_id=batch.batch_id,
                    master_record_id=batch.master_record_id,
                    master_version=batch.master_version,
                    **{name: getattr(step, name) for name in _BATCH_STEP_FIELDS},
                )
            )
            for step in batch.steps
        )
    except (ValidationError, ValueError) as exc:
        raise RebuildError(
            f"stored rows for {batch.batch_id} are invalid: {exc}"
        ) from exc
    matched = match_batches(
        ParseResult(masters={version.key: version}, batch_steps={batch.batch_id: steps})
    )
    if matched.errors or batch.batch_id not in matched.batches:
        reasons = "; ".join(error.reason for error in matched.errors) or "not matched"
        raise RebuildError(f"stored batch {batch.batch_id} did not match: {reasons}")
    return matched.batches[batch.batch_id]


def rereview_batch(batch_id: str, *, current: str | None = None) -> RereviewResult:
    """Append a run for one batch if its latest run used older rules.

    current is the running rule_version (RULE_VERSION when None). An unknown
    batch raises BatchNotFound; stored rows that can't be rebuilt raise
    RebuildError. Both store nothing.
    """
    runs = list_runs(batch_id)
    return _rereview(batch_id, runs[-1] if runs else None, current)


def rereview_all(*, current: str | None = None) -> SyncReport:
    """Re-review every stored batch whose latest run used older rules.

    Each batch is its own transaction, so one failure doesn't undo the others
    and a later call carries on where this one stopped. Store and rebuild
    errors are reported per batch; a lost database connection propagates.
    """
    outcome: dict[str, list] = {
        key: [] for key in ("rereviewed", "current", "newer", "unknown", "failed")
    }
    for batch_id, latest in latest_runs().items():
        try:
            result = _rereview(batch_id, latest, current)
        except StoreError as exc:
            logger.warning("re-review of %s failed: %s", batch_id, exc)
            outcome["failed"].append((batch_id, str(exc)))
            continue
        if result.run_id is not None:
            outcome["rereviewed"].append(batch_id)
        elif result.standing in {Standing.NEWER, Standing.UNKNOWN}:
            outcome[result.standing.name.lower()].append(batch_id)
        else:
            outcome["current"].append(batch_id)
    return SyncReport(**{key: tuple(values) for key, values in outcome.items()})


def _rereview(
    batch_id: str, latest: StoredRun | None, current: str | None
) -> RereviewResult:
    """Re-review one batch whose latest run (or None) was already read."""
    current = running_version() if current is None else current
    latest_version = None if latest is None else latest.rule_version
    where = standing(latest_version, current)
    if not where.moves_forward:
        return RereviewResult(batch_id, where, latest_version)
    batch = get_batch(batch_id)
    master = get_master(batch.master_record_id, batch.master_version)
    reviewed = review_batch(rebuild(master, batch))
    try:
        run_id = PostgresReviewStore().insert_review_run_after(
            batch_id,
            current,
            reviewed.result_category,
            build_payload(reviewed),
            expected_latest_run_id=None if latest is None else latest.run_id,
        )
    except StaleReview:
        return RereviewResult(batch_id, Standing.CURRENT, latest_version)
    return RereviewResult(
        batch_id, where, latest_version, run_id, reviewed.result_category
    )


_MASTER_STEP_FIELDS = (
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
_BATCH_STEP_FIELDS = (
    "step_id",
    "reading",
    "unit",
    "performed_by",
    "performed_at",
    "verified_by",
    "verified_at",
    "comment",
)
