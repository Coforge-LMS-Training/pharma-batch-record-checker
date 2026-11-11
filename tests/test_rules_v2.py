"""Tests for version-2 rules 9 and 10. All data is synthetic.

late_verification (rule 9) and performed_out_of_order (rule 10) are on by
default since rule_version 1.1.0; RulesConfig(enable_v2_rules=False) gives
exactly the 8 v1 rules. Expected behaviour follows tests/generator/rule_oracle.py.
"""

import csv
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType

import pytest

from pharma_batch_record_checker.generator.batches import generate_clean_batch
from pharma_batch_record_checker.generator.cli import main as generate
from pharma_batch_record_checker.generator.masters import build_masters
from pharma_batch_record_checker.matching import MatchedBatch, match_batches
from pharma_batch_record_checker.parser import ParseResult
from pharma_batch_record_checker.records import BatchStep, MasterVersion
from pharma_batch_record_checker.review import review, step_severity
from pharma_batch_record_checker.rules import (
    RULE_IDS,
    V2_RULE_IDS,
    Finding,
    RulesConfig,
    batch_findings,
)

REPO = Path(__file__).resolve().parents[1]
SYN_ROWS = [r for r in build_masters() if r.master_record_id == "MBR-SYN500"]
SYN_V3 = MasterVersion.from_rows(SYN_ROWS)
V2_ON = RulesConfig(enable_v2_rules=True)
V2_OFF = RulesConfig(enable_v2_rules=False)
BATCH = "B26-0413"


def clean() -> list[BatchStep]:
    """A clean SYN500 v3 batch: steps 30 min apart from 07:00, checks 5-20 min after."""
    rows = generate_clean_batch(SYN_ROWS, BATCH, datetime(2026, 10, 13, 7, 0), 1)
    return [BatchStep.from_row(r) for r in rows]


def row(steps: list[BatchStep], step_id: str) -> BatchStep:
    """The row for one step."""
    (found,) = [s for s in steps if s.step_id == step_id]
    return found


def change(steps: list[BatchStep], step_id: str, **update) -> list[BatchStep]:
    """The same rows with one step's values changed."""
    return [s.model_copy(update=update) if s.step_id == step_id else s for s in steps]


def performed(steps: list[BatchStep], step_id: str, at: datetime) -> list[BatchStep]:
    """Move one step's performance to `at`, its verification (if any) with it."""
    old = row(steps, step_id)
    update: dict = {"performed_at": at}
    if old.verified_at is not None:
        update["verified_at"] = old.verified_at + (at - old.performed_at)
    return change(steps, step_id, **update)


def matched(steps: list[BatchStep]) -> MatchedBatch:
    """Match the rows against SYN500 v3 (they must match cleanly)."""
    result = match_batches(
        ParseResult(
            masters=MappingProxyType({SYN_V3.key: SYN_V3}),
            batch_steps=MappingProxyType({BATCH: tuple(steps)}),
            batch_file="batch.csv",
        )
    )
    assert result.errors == ()
    return result.batches[BATCH]


def v2(steps: list[BatchStep], config=V2_ON) -> list[tuple[str, str, str]]:
    """(rule_id, step_id, detail) of every v2 finding, in order."""
    return [
        (f.rule_id, f.step_id, f.detail)
        for f in batch_findings(matched(steps), config)
        if f.rule_id in V2_RULE_IDS
    ]


def rated(findings) -> list[tuple[str, str, str, str]]:
    """(batch_id, step_id, rule_id, step severity), as review.py rates them."""
    by_step: dict[tuple[str, str], list[Finding]] = {}
    for f in findings:
        by_step.setdefault((f.batch_id, f.step_id), []).append(f)
    severity = {key: step_severity(fs) for key, fs in by_step.items()}
    return [
        (f.batch_id, f.step_id, f.rule_id, severity[(f.batch_id, f.step_id)])
        for f in findings
    ]


# --- Rule 9: late_verification ----------------------------------------------------


@pytest.mark.parametrize("minutes, flagged", [(60, False), (61, True)])
def test_verified_exactly_60_minutes_after_passes_61_flags(minutes, flagged):
    """Non-critical S-BLEND-01 performed 10:30, verified 60 or 61 minutes later."""
    at = row(clean(), "S-BLEND-01").performed_at
    steps = change(
        clean(),
        "S-BLEND-01",
        verified_by="OP-146",
        verified_at=at + timedelta(minutes=minutes),
    )
    expected = [
        (
            "late_verification",
            "S-BLEND-01",
            "Verified 11:31, 61 min after performed 10:30 (limit 60 min)",
        )
    ]
    assert v2(steps) == (expected if flagged else [])


# --- Rule 10: performed_out_of_order ---------------------------------------------------


def test_performed_before_the_previous_step_flags():
    """S-BLEND-02 performed 10:10, before S-BLEND-01 at 10:30."""
    steps = performed(clean(), "S-BLEND-02", datetime(2026, 10, 13, 10, 10))
    assert v2(steps) == [
        (
            "performed_out_of_order",
            "S-BLEND-02",
            "Performed 10:10, before S-BLEND-01 (performed 10:30)",
        )
    ]


def test_same_time_as_the_previous_step_passes():
    """S-DISP-02 performed at the same minute as S-DISP-01."""
    steps = performed(clean(), "S-DISP-02", row(clean(), "S-DISP-01").performed_at)
    assert v2(steps) == []


def test_first_step_is_skipped():
    """The first step has no step before it, however early it was performed."""
    steps = performed(clean(), "S-DISP-01", datetime(2026, 10, 13, 3, 0))
    assert v2(steps) == []


def test_missing_previous_step_is_skipped_over():
    """S-BLEND-01 absent: S-BLEND-02 is compared with S-GRAN-05 (10:00)."""
    without = [s for s in clean() if s.step_id != "S-BLEND-01"]
    early = performed(without, "S-BLEND-02", datetime(2026, 10, 13, 9, 50))
    later = performed(without, "S-BLEND-02", datetime(2026, 10, 13, 10, 20))
    assert v2(early) == [
        (
            "performed_out_of_order",
            "S-BLEND-02",
            "Performed 09:50, before S-GRAN-05 (performed 10:00)",
        )
    ]
    assert v2(later) == []


@pytest.mark.parametrize(
    "step_id, update",
    [
        ("S-BLEND-01", {"verified_by": "OP-146", "verified_at": None}),
        ("S-BLEND-02", {"performed_at": None}),
    ],
    ids=["verified_at-blank", "performed_at-blank"],
)
def test_blank_time_skips_both_rules(step_id, update):
    """A blank time skips rules 9 and 10 for that step.

    S-COMP-01 is performed before S-BLEND-01, but its previous present step
    (S-BLEND-02) has no time in the second case, so the rule is skipped, not
    looked up further back (as in the oracle). In the first case it's still
    compared with S-BLEND-02 at 11:00 and flags.
    """
    steps = change(clean(), step_id, **update)
    steps = performed(steps, "S-COMP-01", datetime(2026, 10, 13, 10, 20))
    found = v2(steps)
    assert not [f for f in found if f[1] == step_id]
    expected_comp = [] if step_id == "S-BLEND-02" else ["S-COMP-01"]
    assert [step for _, step, _ in found] == expected_comp


# --- Severity ---------------------------------------------------------------------------


def test_single_v2_finding_on_a_critical_step_is_needs_review():
    """Critical S-COMP-01 verified 65 minutes late: Needs Review."""
    at = row(clean(), "S-COMP-01").performed_at
    steps = change(clean(), "S-COMP-01", verified_at=at + timedelta(minutes=65))
    assert rated(batch_findings(matched(steps), V2_ON)) == [
        (BATCH, "S-COMP-01", "late_verification", "Needs Review")
    ]


def test_both_v2_rules_on_one_step_are_critical():
    """S-COMP-01 performed before S-BLEND-02 and verified 65 minutes late."""
    at = datetime(2026, 10, 13, 10, 50)
    steps = change(
        clean(), "S-COMP-01", performed_at=at, verified_at=at + timedelta(minutes=65)
    )
    assert rated(batch_findings(matched(steps), V2_ON)) == [
        (BATCH, "S-COMP-01", "late_verification", "Critical"),
        (BATCH, "S-COMP-01", "performed_out_of_order", "Critical"),
    ]


# --- Default config ----------------------------------------------------------------------


def test_v2_rules_are_on_by_default_and_can_be_turned_off():
    """The default runs rules 9 and 10; enable_v2_rules=False gives v1 output only."""
    at = datetime(2026, 10, 13, 10, 50)
    steps = change(
        clean(), "S-COMP-01", performed_at=at, verified_at=at + timedelta(minutes=65)
    )
    batch = matched(steps)
    assert batch_findings(batch) == batch_findings(batch, V2_ON)
    assert [f.rule_id for f in batch_findings(batch)] == list(V2_RULE_IDS)
    assert batch_findings(batch, V2_OFF) == ()
    assert set(V2_RULE_IDS) <= set(RULE_IDS)


@pytest.mark.parametrize(
    "minutes, error",
    [(0, ValueError), (-5, ValueError), (True, TypeError), (1.5, TypeError)],
)
def test_config_refuses_a_bad_threshold(minutes, error):
    """The threshold must be a positive whole number of minutes."""
    with pytest.raises(error, match="positive integer"):
        RulesConfig(late_verification_minutes=minutes)


# --- Generated dataset with the catalog ---------------------------------------------------


def test_engine_matches_the_generator_with_v2_rules_on(tmp_path):
    """--batches 14 --catalog catalog: review() equals expected_findings.csv.

    The default config (v2 on) gives every expected row; enable_v2_rules=False
    gives the same rows minus the v2 ones.
    """
    out = tmp_path / "data"
    args = ["--batches", "14", "--catalog", str(REPO / "catalog"), "--out", str(out)]
    assert generate(args) == 0
    master_path, batch_path = out / "master_record.csv", out / "batch_record.csv"
    with open(out / "expected_findings.csv", newline="", encoding="utf-8") as f:
        expected = Counter(
            (r["batch_id"], r["step_id"], r["rule_id"], r["severity"])
            for r in csv.DictReader(f)
        )

    def reviewed(config=None) -> Counter:
        run = review(master_path, batch_path, *([config] if config else []))
        assert (run.errors, run.rule_version) == ((), "1.1.0")
        return Counter(
            (f.batch_id, f.step_id, f.rule_id, f.step_severity)
            for b in run.batches.values()
            for f in b.findings
        )

    default = reviewed()
    assert default == expected
    assert sum(default.values()) == 51
    assert {rule for _, _, rule, _ in default} >= set(V2_RULE_IDS)
    assert reviewed(V2_OFF) == Counter(
        {key: n for key, n in expected.items() if key[2] not in V2_RULE_IDS}
    )
