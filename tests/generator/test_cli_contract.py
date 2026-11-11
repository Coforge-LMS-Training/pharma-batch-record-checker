"""PHA-40: the generator contract, checked on CLI output read back from disk.

Runs generate-batch-records, reads the three CSV files back exactly as a
consumer would, and checks the agreed outcomes with the test-only rule
check: expected findings match, clean batches (boundary readings included)
are unflagged, all 8 rules are planted, and multi-finding steps are Critical.
No rules-engine code is imported.
"""

import csv
from collections import Counter

import pytest

from pharma_batch_record_checker.generator.cli import main
from pharma_batch_record_checker.generator.dataset_io import (
    BATCH_FILE,
    EXPECTED_FILE,
    MASTER_FILE,
)
from pharma_batch_record_checker.generator.expected_findings import (
    CRITICAL,
    V1_RULE_IDS,
)
from pharma_batch_record_checker.schema import BatchRow, MasterRow
from tests.generator.rule_oracle import rule_violations


def read_rows(path):
    """Rows of a CSV file as dicts, read the way any consumer would."""
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module", params=[0, 11, 42], ids=lambda s: f"seed{s}")
def cli_output(request, tmp_path_factory):
    """Files written by `generate-batch-records --batches 8`, read back from disk."""
    out = tmp_path_factory.mktemp(f"seed{request.param}")
    assert (
        main(["--seed", str(request.param), "--batches", "8", "--out", str(out)]) == 0
    )
    masters = [MasterRow(**r) for r in read_rows(out / MASTER_FILE)]
    batches = [BatchRow(**r) for r in read_rows(out / BATCH_FILE)]
    expected = read_rows(out / EXPECTED_FILE)
    return masters, batches, expected


def by_batch(batches):
    """Batch rows grouped by batch_id, in file order."""
    grouped = {}
    for row in batches:
        grouped.setdefault(row.batch_id, []).append(row)
    return grouped


def master_for(masters, rows):
    """The master rows a batch executes."""
    key = (rows[0].master_record_id, rows[0].master_version)
    return [m for m in masters if (m.master_record_id, m.master_version) == key]


def expected_pairs(expected, batch_id):
    """Sorted (step_id, rule_id) the expected file lists for one batch."""
    return sorted(
        (e["step_id"], e["rule_id"]) for e in expected if e["batch_id"] == batch_id
    )


def test_cli_output_expected_findings_match_rule_check(cli_output):
    """For every batch on disk, the rules find exactly what the expected file lists."""
    masters, batches, expected = cli_output
    for batch_id, rows in by_batch(batches).items():
        found = rule_violations(master_for(masters, rows), rows)
        assert sorted(tuple(reversed(f.split(":"))) for f in found) == expected_pairs(
            expected, batch_id
        ), batch_id


def test_cli_output_clean_batches_unflagged(cli_output):
    """Clean batches, boundary readings included, have no expected or found findings."""
    masters, batches, expected = cli_output
    clean = [b for b in by_batch(batches) if not expected_pairs(expected, b)]
    assert len(clean) == 4  # 8 batches, every other one per product is defective
    at_bound = 0
    for batch_id in clean:
        rows = by_batch(batches)[batch_id]
        master = master_for(masters, rows)
        assert rule_violations(master, rows) == []
        steps = {m.step_id: m for m in master}
        at_bound += sum(
            r.reading is not None
            and r.reading in (steps[r.step_id].min_value, steps[r.step_id].max_value)
            for r in rows
        )
    assert at_bound >= 2 * len(clean)  # each clean batch pins a min and a max


def test_cli_output_has_all_eight_rules_and_multi_finding_critical(cli_output):
    """All 8 rules are planted, and every step with 2+ findings is Critical."""
    _, _, expected = cli_output
    assert {e["rule_id"] for e in expected} == set(V1_RULE_IDS)
    per_step = Counter((e["batch_id"], e["step_id"]) for e in expected)
    multi = {key for key, n in per_step.items() if n >= 2}
    assert multi
    assert all(
        e["severity"] == CRITICAL
        for e in expected
        if (e["batch_id"], e["step_id"]) in multi
    )
