"""Store parse and match problems.

A problem row is not a hash-chain event. insert_problems does not append
an audit event, and the event types stay master_import, batch_import,
review_run, and finding_note. Blocked batches are recorded here and are
not stored as review runs.
"""

import uuid
from collections import Counter
from collections.abc import Sequence

from pharma_batch_record_checker.matching import MatchError
from pharma_batch_record_checker.parser import ParseError
from pharma_batch_record_checker.store.connection import transaction

_INSERT = """
INSERT INTO review_problem (
    problem_id, source, file, "row", field, reason, raw_value,
    batch_id, step_id, kind
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_COUNT_SAME = """
SELECT count(*)
FROM review_problem
WHERE source IS NOT DISTINCT FROM %s
  AND file IS NOT DISTINCT FROM %s
  AND "row" IS NOT DISTINCT FROM %s
  AND field IS NOT DISTINCT FROM %s
  AND reason IS NOT DISTINCT FROM %s
  AND raw_value IS NOT DISTINCT FROM %s
  AND batch_id IS NOT DISTINCT FROM %s
  AND step_id IS NOT DISTINCT FROM %s
  AND kind IS NOT DISTINCT FROM %s
"""


def problems_already_stored(errors: Sequence[ParseError | MatchError]) -> bool:
    """True when every error is already in review_problem.

    An empty sequence is not a duplicate. Repeated copies count, and nulls
    match nulls. A new error makes the whole sequence new.
    """
    rows = tuple(_problem_values(error)[1:] for error in errors)
    if not rows:
        return False
    needed = Counter(rows)
    with transaction() as conn:
        for key, count in needed.items():
            stored = conn.execute(_COUNT_SAME, key).fetchone()
            if stored[0] < count:
                return False
    return True


def insert_problems(errors: Sequence[ParseError | MatchError]) -> None:
    """Store each parse or match error. An empty sequence writes nothing.

    recorded_at is the server clock. Parse errors keep file, row, field,
    and raw_value, and their kind is null. Match errors keep reason, kind,
    and any batch or step they name.
    """
    rows = tuple(_problem_values(error) for error in errors)
    if not rows:
        return
    with transaction() as conn:
        for values in rows:
            conn.execute(_INSERT, values)


def _problem_values(
    error: ParseError | MatchError,
) -> tuple[object, ...]:
    """One INSERT parameter tuple, or TypeError for anything else."""
    problem_id = uuid.uuid4()
    if isinstance(error, ParseError):
        return (
            problem_id,
            "parse",
            error.file,
            error.row,
            error.field,
            error.reason,
            error.raw_value,
            error.batch_id,
            error.step_id,
            None,
        )
    if isinstance(error, MatchError):
        return (
            problem_id,
            "match",
            None,
            None,
            None,
            error.reason,
            None,
            error.batch_id,
            error.step_id,
            error.kind,
        )
    raise TypeError(f"not a parse or match error: {type(error).__name__}")
