"""Run a review: parse -> match -> rules -> severity (rule_version 1.1.0).

Severity is decided per step, from the findings the rules already detected.
It never adds a finding: "two problems on one step" is not a rule, it only
makes the step Critical. Every finding on a step carries that step's severity.

- A step is Critical if it has 2 or more findings, or if it is a critical
  step with a finding in CRITICAL_ON_CRITICAL_STEP. Otherwise it is Needs Review.
- A batch's result_category is its worst step severity, or No Findings.
- A run's result_category is the worst of its reviewed batches, so a Critical
  batch is never hidden. A batch blocked by validation errors gets no category
  at all; if any batch was blocked, the run is never reported as No Findings
  (it is None instead), and `complete` is False whenever there are errors.

Findings are ordered by step_seq, then rule_id alphabetically, so the order
can be reproduced from the stored values alone.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Literal, get_args

from pharma_batch_record_checker.matching import MatchedBatch, MatchError, match_batches
from pharma_batch_record_checker.parser import ParseError, Source, parse_files
from pharma_batch_record_checker.records import MasterVersion
from pharma_batch_record_checker.rules import (
    DEFAULT_RULES_CONFIG,
    RULE_VERSION,
    Finding,
    RulesConfig,
    batch_findings,
)

# The two agreed step severities, and the three agreed result categories.
Severity = Literal["Critical", "Needs Review"]
CRITICAL, NEEDS_REVIEW = get_args(Severity)
ResultCategory = Literal["Critical", "Needs Review", "No Findings"]
NO_FINDINGS: ResultCategory = "No Findings"

# PENDING CLIENT CONFIRMATION (open questions 2-3, overall plan v1, section 12):
# missing_step and missing_performer alone stay Needs Review, even on a
# critical step. Only these rules make a critical step Critical on their own
# (a step with 2 or more findings is Critical whatever its rules are).
CRITICAL_ON_CRITICAL_STEP: frozenset[str] = frozenset(
    {"missing_second_check", "same_person", "out_of_range"}
)

_RANK: dict[str, int] = {NO_FINDINGS: 0, NEEDS_REVIEW: 1, CRITICAL: 2}


@dataclass(frozen=True, kw_only=True)
class ReviewFinding(Finding):
    """A finding plus the severity of its step (shared by the step's findings)."""

    step_severity: Severity


@dataclass(frozen=True)
class BatchReview:
    """One reviewed batch: its findings in order, and its result category."""

    batch_id: str
    master: MasterVersion
    findings: tuple[ReviewFinding, ...]
    result_category: ResultCategory


@dataclass(frozen=True)
class ReviewRun:
    """The result of one review run.

    batches holds only the batches that could be reviewed, in file order.
    errors holds every parse and validation error; a batch named there was
    not reviewed. result_category is None only when it would otherwise be
    No Findings while some batch was blocked. complete is False if there are
    any errors.
    """

    rule_version: str
    batches: Mapping[str, BatchReview]
    errors: tuple[ParseError | MatchError, ...]
    result_category: ResultCategory | None
    complete: bool


def review(
    master_path: Source,
    batch_path: Source,
    config: RulesConfig = DEFAULT_RULES_CONFIG,
) -> ReviewRun:
    """Review every batch in batch_path against the masters in master_path.

    config sets the rule options (DEFAULT_RULES_CONFIG runs all 10 rules).
    """
    parsed = parse_files(master_path, batch_path)
    matched = match_batches(parsed)
    batches = {b: review_batch(m, config) for b, m in matched.batches.items()}
    category: ResultCategory | None = _worst(
        b.result_category for b in batches.values()
    )
    if category == NO_FINDINGS and matched.errors:
        category = None  # blocked batches were not reviewed: don't claim clean
    errors = parsed.errors + matched.errors
    return ReviewRun(
        rule_version=RULE_VERSION,
        batches=MappingProxyType(batches),
        errors=errors,
        result_category=category,
        complete=not errors,
    )


def review_batch(
    batch: MatchedBatch, config: RulesConfig = DEFAULT_RULES_CONFIG
) -> BatchReview:
    """Rate each step's findings and order them by step_seq, then rule_id."""
    found = batch_findings(batch, config)
    by_step: dict[str, list[Finding]] = {}
    for finding in found:
        by_step.setdefault(finding.step_id, []).append(finding)
    severity = {step_id: step_severity(fs) for step_id, fs in by_step.items()}
    ordered = sorted(found, key=lambda f: (f.step_seq, f.rule_id))
    findings = tuple(_rated(f, severity[f.step_id]) for f in ordered)
    return BatchReview(
        batch_id=batch.batch_id,
        master=batch.master,
        findings=findings,
        result_category=_worst(severity.values()),
    )


def step_severity(findings: Sequence[Finding]) -> Severity:
    """The agreed per-step severity for a step's findings (at least one)."""
    if len(findings) >= 2:
        return CRITICAL
    if any(f.critical and f.rule_id in CRITICAL_ON_CRITICAL_STEP for f in findings):
        return CRITICAL
    return NEEDS_REVIEW


def _rated(finding: Finding, severity: Severity) -> ReviewFinding:
    """The same finding with its step's severity."""
    values = {f.name: getattr(finding, f.name) for f in fields(Finding)}
    return ReviewFinding(**values, step_severity=severity)


def _worst(categories: Iterable[str]) -> ResultCategory:
    """Critical over Needs Review over No Findings (No Findings when empty)."""
    return max(categories, key=_RANK.__getitem__, default=NO_FINDINGS)
