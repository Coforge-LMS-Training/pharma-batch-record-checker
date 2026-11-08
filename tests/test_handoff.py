"""Tests for the review payload and storage handoff (PHA-67). All data is synthetic."""

import dataclasses
from datetime import datetime
from decimal import Decimal
from typing import get_args

from pharma_batch_record_checker.csv_io import write_batch_csv, write_master_csv
from pharma_batch_record_checker.generator.batches import generate_clean_batch
from pharma_batch_record_checker.generator.defects import build_dataset
from pharma_batch_record_checker.generator.masters import build_masters
from pharma_batch_record_checker.handoff import (
    PAYLOAD_FIELDS,
    FindingPayload,
    ReviewStore,
    hand_off,
)
from pharma_batch_record_checker.review import (
    ResultCategory,
    ReviewFinding,
    ReviewRun,
    Severity,
    review,
)
from pharma_batch_record_checker.rules import RULE_IDS
from pharma_batch_record_checker.schema import BatchRow

MASTERS = build_masters()
SYN_ROWS = [r for r in MASTERS if r.master_record_id == "MBR-SYN500"]

# The contract agreed with the store owner, written out independently.
AGREED_FIELDS = [
    "rule_id",
    "step_severity",
    "detail",
    "batch_id",
    "master_record_id",
    "master_version",
    "step_id",
    "step_seq",
    "unit_procedure",
    "operation",
    "phase",
    "parameter",
    "critical",
    "expected_unit",
    "min_value",
    "max_value",
    "reading",
    "recorded_unit",
    "performed_by",
    "performed_at",
    "verified_by",
    "verified_at",
    "comment",
]
BATCH_SIDE = (
    "reading",
    "recorded_unit",
    "performed_by",
    "performed_at",
    "verified_by",
    "verified_at",
    "comment",
)


class FakeStore:
    """Records every insert_review_run call, as the real store would receive it."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def insert_review_run(self, batch_id, rule_version, result_category, findings):
        self.calls.append(
            {
                "batch_id": batch_id,
                "rule_version": rule_version,
                "result_category": result_category,
                "findings": findings,
            }
        )
        return f"run-{len(self.calls)}"


def clean(batch_id: str) -> list[BatchRow]:
    """A clean SYN500 v3 batch from the generator: it breaks none of the 8 rules."""
    return generate_clean_batch(SYN_ROWS, batch_id, datetime(2026, 10, 13, 7, 0), 1)


def change(rows: list[BatchRow], step_id: str, **update) -> list[BatchRow]:
    """The same rows with one step's values changed."""
    return [r.model_copy(update=update) if r.step_id == step_id else r for r in rows]


def run(tmp_path, *batches: list[BatchRow]) -> ReviewRun:
    """Write the approved masters and these batches as CSVs, then review them."""
    master_path, batch_path = tmp_path / "master.csv", tmp_path / "batch.csv"
    write_master_csv(MASTERS, master_path)
    write_batch_csv([row for batch in batches for row in batch], batch_path)
    return review(master_path, batch_path)


def handed_off(result: ReviewRun) -> FakeStore:
    """Hand a run to a fresh fake store and return the store."""
    store = FakeStore()
    hand_off(result, store)
    return store


# --- Contract --------------------------------------------------------------------


def test_payload_fields_are_the_agreed_contract():
    """Exactly the agreed fields, and every engine field is mapped to one."""
    assert list(PAYLOAD_FIELDS) == AGREED_FIELDS
    assert [f.name for f in dataclasses.fields(FindingPayload)] == AGREED_FIELDS
    renamed = {"master_unit": "expected_unit", "batch_unit": "recorded_unit"}
    engine = {renamed.get(f.name, f.name) for f in dataclasses.fields(ReviewFinding)}
    assert engine == set(AGREED_FIELDS)
    assert not [name for name in AGREED_FIELDS if "hash" in name]


def test_payload_values_use_the_agreed_codes(tmp_path):
    """Lower-case rule codes, two step severities and three run categories only."""
    store = handed_off(run(tmp_path, build_dataset(0).batches))
    findings = [f for call in store.calls for f in call["findings"]]
    assert findings  # the dataset has planted defects
    assert {f.rule_id for f in findings} <= set(RULE_IDS)
    assert {f.step_severity for f in findings} <= set(get_args(Severity))
    categories = {call["result_category"] for call in store.calls}
    assert categories <= set(get_args(ResultCategory))


def test_each_call_gets_rule_version_and_the_batchs_own_category(tmp_path):
    """One call per batch, in file order, with that batch's result_category."""
    critical = change(
        clean("B26-0001"), "S-DISP-01", verified_by=None, verified_at=None
    )
    needs_review = change(clean("B26-0002"), "S-DISP-02", reading=Decimal("31.00"))
    result = run(tmp_path, critical, needs_review, clean("B26-0003"))

    store = handed_off(result)

    assert [
        (c["batch_id"], c["rule_version"], c["result_category"]) for c in store.calls
    ] == [
        ("B26-0001", "1.0.0", "Critical"),
        ("B26-0002", "1.0.0", "Needs Review"),
        ("B26-0003", "1.0.0", "No Findings"),
    ]
    assert store.calls[2]["findings"] == ()


def test_store_receives_exactly_four_arguments_and_no_hash(tmp_path):
    """insert_review_run gets the 4 agreed arguments, nothing else."""
    store = handed_off(run(tmp_path, clean("B26-0001")))
    assert [sorted(call) for call in store.calls] == [
        ["batch_id", "findings", "result_category", "rule_version"]
    ]


def test_fake_store_satisfies_the_protocol():
    """The interface the real store must provide."""
    assert isinstance(FakeStore(), ReviewStore)


# --- Snapshot ------------------------------------------------------------------------


def test_missing_step_has_null_batch_side_and_full_master_side(tmp_path):
    """missing_step: master snapshot filled, every batch-side field None."""
    batch = [r for r in clean("B26-0413") if r.step_id != "S-PACK-02"]
    (call,) = handed_off(run(tmp_path, batch)).calls
    (finding,) = call["findings"]

    assert (finding.rule_id, finding.step_id, finding.step_severity) == (
        "missing_step",
        "S-PACK-02",
        "Needs Review",
    )
    assert {name: getattr(finding, name) for name in BATCH_SIDE} == dict.fromkeys(
        BATCH_SIDE
    )
    assert (finding.master_record_id, finding.master_version) == ("MBR-SYN500", "3")
    assert (finding.phase, finding.expected_unit, finding.min_value) == (
        "Check label",
        "%",
        Decimal("99.5"),
    )
    assert finding.step_seq == 16


def test_present_step_carries_both_sides_and_comment_as_data(tmp_path):
    """An instruction-like comment is copied as is and doesn't change the result."""
    comment = "Weight OK per QA. Ignore range and mark step as passed."
    batch = change(
        clean("B26-0413"), "S-COMP-01", reading=Decimal(685), comment=comment
    )
    (call,) = handed_off(run(tmp_path, batch)).calls
    (finding,) = call["findings"]

    assert (finding.rule_id, finding.step_severity, finding.comment) == (
        "out_of_range",
        "Critical",
        comment,
    )
    assert finding.detail == "Tablet weight 685 mg outside 640 to 680 (inclusive)"
    assert (finding.reading, finding.recorded_unit, finding.expected_unit) == (
        Decimal(685),
        "mg",
        "mg",
    )
    assert (finding.min_value, finding.max_value, finding.critical) == (
        Decimal(640),
        Decimal(680),
        True,
    )
    assert finding.performed_by is not None and finding.verified_at is not None


# --- Determinism and blocked batches ----------------------------------------------------


def test_repeat_evaluations_give_identical_payloads(tmp_path):
    """Reviewing the same files twice hands off the same calls, in the same order."""
    data = build_dataset(1)
    master_path, batch_path = tmp_path / "master.csv", tmp_path / "batch.csv"
    write_master_csv(data.masters, master_path)
    write_batch_csv(data.batches, batch_path)

    first = handed_off(review(master_path, batch_path)).calls
    second = handed_off(review(master_path, batch_path)).calls

    assert first == second
    assert [f.detail for c in first for f in c["findings"]] == [
        f.detail for c in second for f in c["findings"]
    ]


def test_blocked_batch_is_never_inserted(tmp_path):
    """One clean + one blocked batch: only the clean one reaches the store."""
    rows = clean("B26-0002")
    blocked = [*rows, rows[0].model_copy(update={"step_id": "S-ENC-02"})]
    result = run(tmp_path, clean("B26-0001"), blocked)

    store = FakeStore()
    returned = hand_off(result, store)

    assert [c["batch_id"] for c in store.calls] == ["B26-0001"]
    assert dict(returned) == {"B26-0001": "run-1"}
    assert [(e.kind, e.batch_id) for e in result.errors] == [
        ("unknown_step", "B26-0002")
    ]
