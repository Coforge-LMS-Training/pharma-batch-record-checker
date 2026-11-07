"""Test-only reference implementation of the 8 agreed rules (rule_version 1.0.0).

`rule_violations` follows the rules exactly as CLAUDE.md states them. It lives
in the tests only: the real rules engine belongs to the parser/rule-engine
owner, and the generator must never depend on either. Generator tests use it
to check their output against the agreed rules. On top of the 8 rules it
reports two structural problems: `duplicate_row` and `unexpected_row` (a row
whose join key matches no master step).
"""

from pharma_batch_record_checker.schema import BatchRow, MasterRow


def rule_violations(master: list[MasterRow], batch: list[BatchRow]) -> list[str]:
    """Return "rule:step_id" for every broken rule (rule_version 1.0.0)."""
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
    return found


def _join_key(row: MasterRow | BatchRow) -> tuple[str, str, str]:
    """The agreed join key: (master_record_id, master_version, step_id)."""
    return (row.master_record_id, row.master_version, row.step_id)
