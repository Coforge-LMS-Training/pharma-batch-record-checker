"""PHA-37 acceptance: the defect dataset covers all 8 rules with traceable findings.

Expected findings are produced while planting (DefectLog). The test-only rule
oracle is run here only to confirm they match; the generator never runs it.
"""

import dataclasses
from collections import Counter

import pytest

import pharma_batch_record_checker.generator.defects as defects_module
from pharma_batch_record_checker.generator.defects import (
    BATCH_PLANS,
    INSTRUCTION_COMMENTS,
    build_dataset,
)
from pharma_batch_record_checker.generator.expected_findings import (
    CRITICAL,
    NEEDS_REVIEW,
    V1_RULE_IDS,
)
from pharma_batch_record_checker.generator.masters import PLC250, SYN500
from tests.generator.rule_oracle import rule_violations
from tests.import_guard import disallowed_imports

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
    assert {f.rule_id for f in DATASET.findings} == set(V1_RULE_IDS)


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


DEFECTS_ALLOWED_PROJECT = {
    "pharma_batch_record_checker.schema",
    "pharma_batch_record_checker.csv_io",
    "pharma_batch_record_checker.generator.batches",
    "pharma_batch_record_checker.generator.masters",
    "pharma_batch_record_checker.generator.expected_findings",
}


def test_defects_module_imports_no_rules_engine():
    """Planting never imports a rules engine or the test oracle, even indirectly."""
    assert (
        disallowed_imports(
            "pharma_batch_record_checker.generator.defects", DEFECTS_ALLOWED_PROJECT
        )
        == set()
    )


# --- Review fix B: dataset correctness --------------------------------------


def test_sibling_batches_have_different_readings():
    """Batches of the same product get their own clean readings."""
    batches = batches_by_id(DATASET)
    clean = {r.step_id: r.reading for r in batches["B26-0001"]}
    defect = {r.step_id: r.reading for r in batches["B26-0002"]}
    planted = {f.step_id for f in DATASET.findings if f.batch_id == "B26-0002"}
    unplanted = [s for s in clean if s not in planted and clean[s] is not None]
    assert any(clean[s] != defect[s] for s in unplanted)


def test_dataset_masters_follow_plans():
    """The masters returned are exactly those the planned batches execute."""
    plans = [p for p in BATCH_PLANS if p.product is PLC250]
    dataset = build_dataset(0, plans)
    assert {m.master_record_id for m in dataset.masters} == {"MBR-PLC250"}
    assert len(dataset.masters) == 10


def test_rejects_duplicate_batch_ids():
    """Two plans with the same batch_id would merge into one batch; refuse them."""
    with pytest.raises(ValueError, match="B26-0001"):
        build_dataset(0, [BATCH_PLANS[0], BATCH_PLANS[0]])


def test_batch_plan_comments_are_read_only():
    """The module-level plans can't be changed in place."""
    with pytest.raises(TypeError):
        BATCH_PLANS[2].comments["S-PACK-02"] = "changed"


# --- PHA-37 follow-up B: dataset and config ---------------------------------


def test_batch_plan_is_hashable():
    """Plans can be used in sets and as dict keys."""
    assert len(set(BATCH_PLANS)) == len(BATCH_PLANS)


def test_equal_product_instances_share_one_master():
    """An equal but separate ProductConfig is the same master, built once."""
    twin = dataclasses.replace(SYN500)
    plans = [
        dataclasses.replace(BATCH_PLANS[0], product=SYN500),
        dataclasses.replace(BATCH_PLANS[2], product=twin),
    ]
    dataset = build_dataset(0, plans)
    assert len(dataset.masters) == 16


def test_conflicting_products_with_same_key_rejected():
    """Two different configs claiming one master id and version are refused."""
    clash = dataclasses.replace(SYN500, product_name="Synthetol 500 mg tablets v2")
    plans = [
        dataclasses.replace(BATCH_PLANS[0], product=SYN500),
        dataclasses.replace(BATCH_PLANS[0], batch_id="B26-0009", product=clash),
    ]
    with pytest.raises(ValueError, match="MBR-SYN500 v3"):
        build_dataset(0, plans)


def test_each_master_built_once(monkeypatch):
    """Each product's master is built once and reused for its batches."""
    calls = []
    real = defects_module.build_master
    monkeypatch.setattr(
        defects_module, "build_master", lambda p: (calls.append(p), real(p))[1]
    )
    build_dataset(0)
    assert len(calls) == 2
