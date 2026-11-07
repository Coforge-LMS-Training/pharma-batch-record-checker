"""PHA-37 acceptance: the defect dataset covers all 8 rules with traceable findings.

Expected findings are produced while planting (DefectLog). The test-only rule
oracle is run here only to confirm they match; the generator never runs it.
"""

import ast
import sys
from collections import Counter
from pathlib import Path

import pytest
from rule_oracle import rule_violations

import pharma_batch_record_checker.defects as defects_module
from pharma_batch_record_checker.defects import INSTRUCTION_COMMENTS, build_dataset
from pharma_batch_record_checker.expected_findings import (
    CRITICAL,
    NEEDS_REVIEW,
    RULE_IDS,
)

DATASET = build_dataset(seed=0)


def batches_by_id(dataset):
    """Batch rows grouped by batch_id, in order of first appearance."""
    grouped = {}
    for row in dataset.batches:
        grouped.setdefault(row.batch_id, []).append(row)
    return grouped


def master_for(dataset, rows):
    """The master rows that a batch executes."""
    key = (rows[0].master_record_id, rows[0].master_version)
    return [m for m in dataset.masters if (m.master_record_id, m.master_version) == key]


def expected_pairs(dataset, batch_id):
    """Sorted (step_id, rule_id) pairs the expected file lists for one batch."""
    return sorted(
        (f.step_id, f.rule_id) for f in dataset.findings if f.batch_id == batch_id
    )


def oracle_pairs(dataset, batch_id, rows):
    """Sorted (step_id, rule_id) pairs the test oracle finds in one batch."""
    found = rule_violations(master_for(dataset, rows), rows)
    return sorted(tuple(reversed(f.split(":"))) for f in found)


def test_all_eight_rule_ids_planted():
    """Every agreed rule has at least one planted case."""
    assert {f.rule_id for f in DATASET.findings} == set(RULE_IDS)


def test_two_defect_steps_present_and_critical():
    """At least two steps carry two findings, and those are all Critical."""
    per_step = Counter((f.batch_id, f.step_id) for f in DATASET.findings)
    multi = {key for key, n in per_step.items() if n >= 2}
    assert len(multi) >= 2
    assert all(
        f.severity == CRITICAL
        for f in DATASET.findings
        if (f.batch_id, f.step_id) in multi
    )


def test_unit_variants_present():
    """Unit mismatches include a blank unit, a wrong case and a different unit."""
    masters = {(m.master_record_id, m.step_id): m.unit for m in DATASET.masters}
    kinds = set()
    for row in DATASET.batches:
        master_unit = masters[(row.master_record_id, row.step_id)]
        if row.unit == master_unit:
            continue
        if row.unit is None:
            kinds.add("blank")
        elif master_unit and row.unit.lower() == master_unit.lower():
            kinds.add("case")
        else:
            kinds.add("other")
    assert kinds == {"blank", "case", "other"}


def test_instruction_comments_only_on_defective_steps():
    """Instruction-like comments appear, and only on steps with expected findings."""
    flagged = {(f.batch_id, f.step_id) for f in DATASET.findings}
    commented = {
        (r.batch_id, r.step_id)
        for r in DATASET.batches
        if r.comment in INSTRUCTION_COMMENTS
    }
    assert commented
    assert commented <= flagged


def test_expected_findings_match_oracle_per_batch():
    """For every batch, the expected file lists exactly what the rules find."""
    for batch_id, rows in batches_by_id(DATASET).items():
        assert oracle_pairs(DATASET, batch_id, rows) == expected_pairs(
            DATASET, batch_id
        )


def test_severity_follows_policy():
    """Severity recomputed from the master flags and finding counts matches."""
    critical_steps = {
        (m.master_record_id, m.step_id) for m in DATASET.masters if m.critical
    }
    master_of = {r.batch_id: r.master_record_id for r in DATASET.batches}
    per_step = Counter((f.batch_id, f.step_id) for f in DATASET.findings)
    by_step = {}
    for f in DATASET.findings:
        by_step.setdefault((f.batch_id, f.step_id), set()).add(f.rule_id)
    for f in DATASET.findings:
        key = (f.batch_id, f.step_id)
        is_critical_step = (master_of[f.batch_id], f.step_id) in critical_steps
        policy_hit = by_step[key] & {
            "missing_second_check",
            "same_person",
            "out_of_range",
        }
        want = (
            CRITICAL
            if per_step[key] >= 2 or (is_critical_step and policy_hit)
            else NEEDS_REVIEW
        )
        assert f.severity == want, f


def test_clean_batches_have_no_findings():
    """The dataset also contains clean batches with nothing expected or found."""
    clean = [
        batch_id
        for batch_id, rows in batches_by_id(DATASET).items()
        if not expected_pairs(DATASET, batch_id)
    ]
    assert len(clean) >= 2
    for batch_id in clean:
        assert oracle_pairs(DATASET, batch_id, batches_by_id(DATASET)[batch_id]) == []


def test_both_products_have_defect_batches():
    """Defects are planted in batches of both the tablet and the capsule master."""
    master_of = {r.batch_id: r.master_record_id for r in DATASET.batches}
    assert {master_of[f.batch_id] for f in DATASET.findings} == {
        "MBR-SYN500",
        "MBR-PLC250",
    }


def test_dataset_is_reproducible():
    """The same seed gives the same masters, batches and findings."""
    assert build_dataset(seed=0) == DATASET
    assert build_dataset(seed=1) != DATASET


@pytest.mark.parametrize("seed", range(10))
def test_expected_findings_match_oracle_for_any_seed(seed):
    """Planting stays exact whatever clean readings the seed produced."""
    dataset = build_dataset(seed)
    for batch_id, rows in batches_by_id(dataset).items():
        assert oracle_pairs(dataset, batch_id, rows) == expected_pairs(
            dataset, batch_id
        )


ALLOWED_PROJECT_IMPORTS = {
    "pharma_batch_record_checker.schema",
    "pharma_batch_record_checker.csv_io",
    "pharma_batch_record_checker.batches",
    "pharma_batch_record_checker.masters",
    "pharma_batch_record_checker.expected_findings",
}


def test_defects_module_imports_no_rules_engine():
    """Planting never imports a rules engine or the test oracle."""
    tree = ast.parse(Path(defects_module.__file__).read_text("utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    disallowed = {
        name
        for name in imported
        if name.split(".")[0] not in sys.stdlib_module_names
        and name.split(".")[0] != "pydantic"
        and name not in ALLOWED_PROJECT_IMPORTS
    }
    assert disallowed == set()
