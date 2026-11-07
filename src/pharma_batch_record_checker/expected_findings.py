"""expected_findings.csv: the answer key for planted defects.

Expected findings are derived only from the defects the generator plants,
never from a rules engine's output, so the file can be used to check that
engine independently. Columns: batch_id, step_id, rule_id, severity.
"""

from typing import Literal, get_args

from pharma_batch_record_checker.schema import Text, _check_header, _CsvRow

EXPECTED_FINDINGS_HEADER: tuple[str, ...] = (
    "batch_id",
    "step_id",
    "rule_id",
    "severity",
)

# The eight confirmed rule_id spellings (rule_version 1.0.0).
RuleId = Literal[
    "out_of_range",
    "missing_reading",
    "missing_step",
    "missing_performer",
    "missing_second_check",
    "same_person",
    "verifier_before_performer",
    "unit_mismatch",
]
RULE_IDS: tuple[str, ...] = get_args(RuleId)

# The two agreed severity values. Severity is per step: every finding on a
# step carries that step's severity.
Severity = Literal["Critical", "Needs Review"]
CRITICAL, NEEDS_REVIEW = get_args(Severity)


class ExpectedFinding(_CsvRow):
    """One row of expected_findings.csv: a finding a planted defect must cause."""

    batch_id: Text
    step_id: Text
    rule_id: RuleId
    severity: Severity


_check_header(ExpectedFinding, EXPECTED_FINDINGS_HEADER)
