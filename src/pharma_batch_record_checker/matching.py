"""Match executed batches to their master steps (after parsing, before rules).

For each batch, the master version is resolved by its full key
(master_record_id, master_version), then every master step is paired with the
batch row for the same step_id, or None when the batch has no row for it.
Pairs come in step_seq order so rule evaluation is deterministic. Absent
steps are passed on as None for the rule layer's missing_step finding.

Problems with the input itself are validation errors (`MatchError`), never
findings, and they use no rule IDs. Pending client confirmation, a batch with
any validation error is not matched at all, so a malformed or ambiguous batch
can't produce a wrong finding:
- unknown_master: no loaded master has the batch's id and version;
- mixed_master: the batch's rows name more than one master version;
- unknown_step: a batch row's step_id is not in its master version;
- duplicate_step: a step_id appears more than once in the batch;
- parse_error: the batch has invalid rows (ParseError with its batch_id), or
  the batch file has parse errors that can't be tied to any batch, in which
  case no batch is matched.

There are no rules or severity here.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, NamedTuple

from pharma_batch_record_checker.parser import ParseResult
from pharma_batch_record_checker.records import (
    BatchStep,
    MasterKey,
    MasterStep,
    MasterVersion,
)

# Validation error kinds. These are not rule IDs and never become findings.
MatchErrorKind = Literal[
    "unknown_master",
    "mixed_master",
    "unknown_step",
    "duplicate_step",
    "parse_error",
]


class StepPair(NamedTuple):
    """A master step and the batch row executing it (None if absent)."""

    master: MasterStep
    batch: BatchStep | None


@dataclass(frozen=True)
class MatchedBatch:
    """A batch ready for rule evaluation: one pair per master step, in order."""

    batch_id: str
    master: MasterVersion
    pairs: tuple[StepPair, ...]

    @property
    def missing_steps(self) -> tuple[MasterStep, ...]:
        """Master steps with no batch row, in step_seq order."""
        return tuple(pair.master for pair in self.pairs if pair.batch is None)


@dataclass(frozen=True)
class MatchError:
    """A batch (or the batch file) that can't be matched.

    batch_id is None only when the problem can't be tied to one batch.
    step_id is set for unknown_step and duplicate_step.
    """

    kind: MatchErrorKind
    batch_id: str | None
    step_id: str | None
    reason: str


@dataclass(frozen=True)
class MatchResult:
    """Matched batches in file order, and every validation error."""

    batches: Mapping[str, MatchedBatch]
    errors: tuple[MatchError, ...]


def match_batches(parsed: ParseResult) -> MatchResult:
    """Pair every batch with its master version's steps.

    Batches come in file order. A batch with any validation error is left
    out of `batches` and reported in `errors` instead.
    """
    invalid_rows: Counter[str] = Counter()
    unattributed = 0
    for error in parsed.errors:
        if error.batch_id is not None:
            invalid_rows[error.batch_id] += 1
        elif parsed.batch_file is not None and error.file == parsed.batch_file:
            unattributed += 1

    errors: list[MatchError] = []
    batches: dict[str, MatchedBatch] = {}
    # A batch whose rows are all invalid still appears, via its parse errors.
    for batch_id in dict.fromkeys([*parsed.batch_steps, *invalid_rows]):
        steps = parsed.batch_steps.get(batch_id, ())
        batch_errors = _check_batch(batch_id, steps, invalid_rows[batch_id], parsed)
        errors += batch_errors
        if not batch_errors:
            batches[batch_id] = _pair(batch_id, steps, parsed.masters)

    if unattributed:
        errors.append(
            MatchError(
                "parse_error",
                None,
                None,
                f"no batch matched: {_plural(unattributed, 'parse error')} in the "
                "batch file can't be tied to a batch",
            )
        )
        batches = {}
    return MatchResult(batches=MappingProxyType(batches), errors=tuple(errors))


def _check_batch(
    batch_id: str,
    steps: Sequence[BatchStep],
    invalid_rows: int,
    parsed: ParseResult,
) -> list[MatchError]:
    """Every validation error for one batch (empty if it can be matched)."""
    errors: list[MatchError] = []
    if invalid_rows:
        errors.append(
            MatchError(
                "parse_error",
                batch_id,
                None,
                f"batch {batch_id} not matched: {_plural(invalid_rows, 'invalid row')}",
            )
        )
    if not steps:
        return errors

    keys = list(dict.fromkeys(_master_key(s) for s in steps))
    if len(keys) > 1:
        names = ", ".join(_name(k) for k in keys)
        reason = f"batch {batch_id} names more than one master: {names}"
        return [*errors, MatchError("mixed_master", batch_id, None, reason)]
    master = parsed.masters.get(keys[0])
    if master is None:
        reason = f"batch {batch_id}: no loaded master {_name(keys[0])}"
        return [*errors, MatchError("unknown_master", batch_id, None, reason)]

    known = {step.step_id for step in master.steps}
    for step_id, count in Counter(s.step_id for s in steps).items():
        if step_id not in known:
            reason = f"batch {batch_id}: step {step_id} is not in {_name(keys[0])}"
            errors.append(MatchError("unknown_step", batch_id, step_id, reason))
        elif count > 1:
            reason = f"batch {batch_id}: step {step_id} appears {count} times"
            errors.append(MatchError("duplicate_step", batch_id, step_id, reason))
    return errors


def _pair(
    batch_id: str,
    steps: Sequence[BatchStep],
    masters: Mapping[MasterKey, MasterVersion],
) -> MatchedBatch:
    """Pair each master step (step_seq order) with its batch row or None.

    Only called for a batch that passed `_check_batch`: one known master,
    every step_id known and unique.
    """
    master = masters[_master_key(steps[0])]
    by_id = {step.step_id: step for step in steps}
    pairs = tuple(StepPair(m, by_id.get(m.step_id)) for m in master.steps)
    return MatchedBatch(batch_id=batch_id, master=master, pairs=pairs)


def _master_key(step: BatchStep) -> MasterKey:
    """The master version a batch row says it executed."""
    return MasterKey(step.master_record_id, step.master_version)


def _name(key: MasterKey) -> str:
    """'MBR-SYN500 v3'."""
    return f"{key.master_record_id} v{key.master_version}"


def _plural(count: int, noun: str) -> str:
    """'1 invalid row' or '2 invalid rows'."""
    return f"{count} {noun}{'' if count == 1 else 's'}"
