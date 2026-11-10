"""Load two CSV files, review them, and store the result.

The steps are the ones in the database plan:

1. review parses, matches, and rates.
2. Insert each master version the parser loaded.
3. Insert each reviewed batch's executed steps. Those steps come from the
   matched batch, which is the full step list, not only the findings.
4. hand_off inserts one review run per reviewed batch.
5. Insert run.errors as review_problem rows.

A second call with the same files raises DuplicateRecord. That includes a
file pair that stored only problems. Records are not overwritten. A later
review of a batch that is already stored is a separate insert_review_run,
which appends a run. This module does not change handoff.py, and a problem
row is not a hash event.
"""

from pharma_batch_record_checker.handoff import ReviewStore, hand_off
from pharma_batch_record_checker.matching import MatchedBatch, match_batches
from pharma_batch_record_checker.parser import Source, parse_files
from pharma_batch_record_checker.records import BatchStep, MasterVersion
from pharma_batch_record_checker.review import ReviewRun, review
from pharma_batch_record_checker.schema import MasterRow
from pharma_batch_record_checker.store.errors import DuplicateRecord
from pharma_batch_record_checker.store.imports import insert_batch, insert_master
from pharma_batch_record_checker.store.problems import (
    insert_problems,
    problems_already_stored,
)


def load_and_review(
    master_path: Source,
    batch_path: Source,
    store: ReviewStore,
) -> ReviewRun:
    """Review the two files and store masters, executed steps, runs, and problems.

    Returns the review run. Blocked batches stay in run.errors and are written
    only as problem rows.
    """
    run = review(master_path, batch_path)
    parsed = parse_files(master_path, batch_path)
    matched = match_batches(parsed)
    for version in parsed.masters.values():
        insert_master(_master_rows(version))
    for batch_id in run.batches:
        try:
            matched_batch = matched.batches[batch_id]
        except KeyError as exc:
            raise RuntimeError(
                f"reviewed batch {batch_id} has no matched steps"
            ) from exc
        insert_batch(_executed_steps(matched_batch))
    hand_off(run, store)
    if not parsed.masters and not run.batches and problems_already_stored(run.errors):
        raise DuplicateRecord("these problems are already stored")
    insert_problems(run.errors)
    return run


def _master_rows(version: MasterVersion) -> list[MasterRow]:
    """MasterRow values for one version the parser already accepted."""
    return [
        MasterRow(
            master_record_id=version.master_record_id,
            master_version=version.master_version,
            product_name=version.product_name,
            batch_size=version.batch_size,
            batch_size_unit=version.batch_size_unit,
            step_seq=step.step_seq,
            step_id=step.step_id,
            unit_procedure=step.unit_procedure,
            operation=step.operation,
            phase=step.phase,
            parameter=step.parameter,
            unit=step.unit,
            min_value=step.min_value,
            max_value=step.max_value,
            critical=step.critical,
        )
        for step in version.steps
    ]


def _executed_steps(batch: MatchedBatch) -> tuple[BatchStep, ...]:
    """Executed rows only. A missing master step has no batch row to store."""
    steps = tuple(pair.batch for pair in batch.pairs if pair.batch is not None)
    if not steps:
        raise ValueError(f"batch {batch.batch_id} has no executed steps")
    return steps
