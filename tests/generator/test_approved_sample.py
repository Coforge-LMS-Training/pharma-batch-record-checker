"""PHA-39: the approved client sample, rebuilt from our own generator.

Same batch IDs and the same flagged steps and rules as the client's sample;
readings and operator IDs are our own synthetic values. The client file in
sample/ is never read here.
"""

import re
from pathlib import Path

import pytest

from pharma_batch_record_checker.generator.approved_sample import (
    BATCH_FILE,
    EXPECTED_FILE,
    MASTER_FILE,
    build_approved_sample,
    write_approved_sample,
)
from pharma_batch_record_checker.generator.defects import INSTRUCTION_COMMENTS
from pharma_batch_record_checker.generator.expected_findings import (
    CRITICAL,
    EXPECTED_FINDINGS_HEADER,
    NEEDS_REVIEW,
)
from pharma_batch_record_checker.schema import BATCH_HEADER, MASTER_HEADER
from tests.generator.rule_oracle import rule_violations

SAMPLE = build_approved_sample()
BATCH_IDS = ("B26-0412", "B26-0413", "P26-0201", "P26-0202")
CLEAN = ("B26-0412", "P26-0201")

# The client sample's defect profile: (step_id, rule_id) -> severity per batch.
EXPECTED = {
    "B26-0413": {
        ("S-DISP-01", "missing_second_check"): CRITICAL,
        ("S-DISP-02", "missing_performer"): NEEDS_REVIEW,
        ("S-GRAN-03", "missing_reading"): NEEDS_REVIEW,
        ("S-GRAN-04", "unit_mismatch"): NEEDS_REVIEW,
        ("S-BLEND-01", "verifier_before_performer"): NEEDS_REVIEW,
        ("S-COMP-01", "out_of_range"): CRITICAL,
        ("S-COMP-03", "unit_mismatch"): NEEDS_REVIEW,
        ("S-COAT-02", "out_of_range"): CRITICAL,
        ("S-COAT-02", "same_person"): CRITICAL,
        ("S-PACK-02", "missing_step"): NEEDS_REVIEW,
    },
    "P26-0202": {
        ("S-BLEND-01", "out_of_range"): NEEDS_REVIEW,
        ("S-ENC-02", "same_person"): CRITICAL,
        ("S-ENC-03", "out_of_range"): NEEDS_REVIEW,
        ("S-PACK-02", "out_of_range"): CRITICAL,
    },
}


def rows_of(batch_id):
    """The batch rows of one sample batch."""
    return [r for r in SAMPLE.batches if r.batch_id == batch_id]


def findings_of(batch_id):
    """{(step_id, rule_id): severity} the expected file lists for one batch."""
    return {
        (f.step_id, f.rule_id): f.severity
        for f in SAMPLE.findings
        if f.batch_id == batch_id
    }


def master_of(batch_id):
    """The master rows a sample batch executes."""
    key = (rows_of(batch_id)[0].master_record_id, rows_of(batch_id)[0].master_version)
    return [m for m in SAMPLE.masters if (m.master_record_id, m.master_version) == key]


def test_four_named_batches_present():
    """Exactly the four approved batch IDs, in order."""
    assert list(dict.fromkeys(r.batch_id for r in SAMPLE.batches)) == list(BATCH_IDS)


def test_masters_are_the_approved_ones():
    """Synthetol MBR-SYN500 v3 (16 steps) and Placeprol MBR-PLC250 v1 (10 steps)."""
    by_master = {}
    for m in SAMPLE.masters:
        by_master.setdefault((m.master_record_id, m.master_version, m.product_name), 0)
        by_master[(m.master_record_id, m.master_version, m.product_name)] += 1
    assert by_master == {
        ("MBR-SYN500", "3", "Synthetol 500 mg film-coated tablets"): 16,
        ("MBR-PLC250", "1", "Placeprol 250 mg hard capsules"): 10,
    }


@pytest.mark.parametrize("batch_id", CLEAN)
def test_clean_batches_have_no_expected_findings(batch_id):
    """B26-0412 and P26-0201 are clean: nothing expected, nothing found."""
    assert findings_of(batch_id) == {}
    assert rule_violations(master_of(batch_id), rows_of(batch_id)) == []


@pytest.mark.parametrize(("batch_id", "steps"), [("B26-0413", 9), ("P26-0202", 4)])
def test_flagged_step_counts(batch_id, steps):
    """B26-0413 has 9 distinct flagged steps, P26-0202 has 4."""
    assert len({step for step, _ in findings_of(batch_id)}) == steps


@pytest.mark.parametrize("batch_id", list(EXPECTED))
def test_defect_profile_matches_client_sample(batch_id):
    """The same steps break the same rules as in the client's sample."""
    assert set(findings_of(batch_id)) == set(EXPECTED[batch_id])


@pytest.mark.parametrize("batch_id", list(EXPECTED))
def test_severities_follow_policy(batch_id):
    """Per-step severities follow the policy; both defective batches carry Critical."""
    assert findings_of(batch_id) == EXPECTED[batch_id]
    assert CRITICAL in findings_of(batch_id).values()


@pytest.mark.parametrize("batch_id", BATCH_IDS)
def test_expected_findings_match_rule_check(batch_id):
    """The test-only rule check finds exactly what the expected file lists."""
    found = rule_violations(master_of(batch_id), rows_of(batch_id))
    assert sorted(tuple(reversed(f.split(":"))) for f in found) == sorted(
        findings_of(batch_id)
    )


def test_instruction_comments_only_on_flagged_steps():
    """Instruction-like comments appear, and only on steps with expected findings."""
    flagged = {(f.batch_id, f.step_id) for f in SAMPLE.findings}
    commented = {
        (r.batch_id, r.step_id)
        for r in SAMPLE.batches
        if r.comment in INSTRUCTION_COMMENTS
    }
    assert commented == {("B26-0413", "S-COMP-01"), ("P26-0202", "S-PACK-02")}
    assert commented <= flagged


def test_fixtures_are_synthetic():
    """Only synthetic OP-nnn operators and the approved synthetic products appear."""
    people = {r.performed_by for r in SAMPLE.batches} | {
        r.verified_by for r in SAMPLE.batches
    }
    assert all(re.fullmatch(r"OP-[0-9]{3}", p) for p in people - {None})
    assert {m.product_name for m in SAMPLE.masters} == {
        "Synthetol 500 mg film-coated tablets",
        "Placeprol 250 mg hard capsules",
    }


# --- Committed fixture files -------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "approved_sample"
FIXTURE_HEADERS = {
    MASTER_FILE: ",".join(MASTER_HEADER),
    BATCH_FILE: ",".join(BATCH_HEADER),
    EXPECTED_FILE: ",".join(EXPECTED_FINDINGS_HEADER),
}


@pytest.mark.parametrize(("name", "header"), FIXTURE_HEADERS.items())
def test_fixture_files_have_exact_headers(name, header):
    """Each committed file starts with its exact header and uses CRLF only."""
    data = (FIXTURE_DIR / name).read_bytes()
    assert data.split(b"\r\n")[0].decode("utf-8") == header
    assert data.count(b"\n") == data.count(b"\r\n")


@pytest.mark.parametrize("name", list(FIXTURE_HEADERS))
def test_committed_fixtures_match_generator(name, tmp_path):
    """The committed files are exactly what the generator writes (no drift)."""
    write_approved_sample(tmp_path)
    assert (FIXTURE_DIR / name).read_bytes() == (tmp_path / name).read_bytes()
