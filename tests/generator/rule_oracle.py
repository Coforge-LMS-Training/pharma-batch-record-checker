"""Test-only reference implementation of the agreed rules.

The 8 rules of rule_version 1.0.0, plus the two version-2 rules the
generator can already plant: late_verification (rule 9: verified more than
LATE_VERIFICATION_MINUTES after performed) and performed_out_of_order (rule 10:
performed before the previous master step present in the batch).

`rule_violations` follows the rules exactly as CLAUDE.md states them. It lives
in the tests only: the real rules engine belongs to the parser/rule-engine
owner, and the generator must never depend on either. Generator tests use it
to check their output against the agreed rules. On top of the 8 rules it
reports two structural problems: `duplicate_row` and `unexpected_row` (a row
whose join key matches no master step).
"""

from datetime import timedelta

from pharma_batch_record_checker.schema import BatchRow, MasterRow

# Rule 9's threshold (config setting late_verification_minutes).
LATE_VERIFICATION_MINUTES = 60


def rule_violations(master: list[MasterRow], batch: list[BatchRow]) -> list[str]:
    """Return "rule:step_id" for every broken rule: the 8 v1 rules, then rules 9-10."""
    found = []
    master_keys = {_join_key(step) for step in master}
    by_key: dict[tuple[str, str, str], BatchRow] = {}
    for row in batch:
        key = _join_key(row)
        if key in by_key:
            found.append(f"duplicate_row:{row.step_id}")
        elif key not in master_keys:
            found.append(f"unexpected_row:{row.step_id}")
        else:
            by_key[key] = row
    for step in master:
        row = by_key.get(_join_key(step))
        if row is None:
            found.append(f"missing_step:{step.step_id}")
            continue
        unit_ok = row.unit == step.unit
        if not unit_ok:
            found.append(f"unit_mismatch:{step.step_id}")
        if step.parameter is not None and row.reading is None:
            found.append(f"missing_reading:{step.step_id}")
        # The range check is skipped when the reading is missing or the unit mismatches.
        if row.reading is not None and unit_ok:
            too_low = step.min_value is not None and row.reading < step.min_value
            too_high = step.max_value is not None and row.reading > step.max_value
            if too_low or too_high:
                found.append(f"out_of_range:{step.step_id}")
        if row.performed_by is None:
            found.append(f"missing_performer:{step.step_id}")
        if step.critical:
            if row.verified_by is None or row.verified_at is None:
                found.append(f"missing_second_check:{step.step_id}")
            elif row.verified_by == row.performed_by:
                found.append(f"same_person:{step.step_id}")
        if (
            row.performed_at is not None
            and row.verified_at is not None
            and row.verified_at < row.performed_at
        ):
            found.append(f"verifier_before_performer:{step.step_id}")
    found += _v2_violations(master, by_key)
    return found


def _v2_violations(
    master: list[MasterRow], by_key: dict[tuple[str, str, str], BatchRow]
) -> list[str]:
    """Rules 9 and 10, walking the master steps present in the batch by step_seq.

    late_verification: verified more than the threshold after performed;
    exactly the threshold passes; skipped if either time is blank.
    performed_out_of_order: performed before the previous master step present
    in the batch; the same time passes; skipped if either time is blank and
    for the first step present.
    """
    found = []
    late = timedelta(minutes=LATE_VERIFICATION_MINUTES)
    previous: BatchRow | None = None
    for step in sorted(master, key=lambda s: s.step_seq):
        row = by_key.get(_join_key(step))
        if row is None:
            continue
        done, checked = row.performed_at, row.verified_at
        if done is not None and checked is not None and checked - done > late:
            found.append(f"late_verification:{step.step_id}")
        if (
            previous is not None
            and done is not None
            and previous.performed_at is not None
            and done < previous.performed_at
        ):
            found.append(f"performed_out_of_order:{step.step_id}")
        previous = row
    return found


def _join_key(row: MasterRow | BatchRow) -> tuple[str, str, str]:
    """The agreed join key: (master_record_id, master_version, step_id)."""
    return (row.master_record_id, row.master_version, row.step_id)
