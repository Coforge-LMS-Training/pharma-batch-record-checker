"""Tests for the review rules (PHA-62 to PHA-65). All data is synthetic."""

import dataclasses
from datetime import datetime, timedelta
from decimal import Decimal
from types import MappingProxyType

import pytest

from pharma_batch_record_checker import rules
from pharma_batch_record_checker.csv_io import write_batch_csv, write_master_csv
from pharma_batch_record_checker.generator.batches import generate_clean_batch
from pharma_batch_record_checker.generator.expected_findings import (
    RULE_IDS as GENERATOR_RULE_IDS,
)
from pharma_batch_record_checker.generator.masters import build_masters
from pharma_batch_record_checker.matching import StepPair, match_batches
from pharma_batch_record_checker.parser import ParseResult, parse_files
from pharma_batch_record_checker.records import (
    BatchStep,
    MasterKey,
    MasterStep,
    MasterVersion,
)
from pharma_batch_record_checker.rules import batch_findings, step_findings
from pharma_batch_record_checker.schema import BatchRow, MasterRow

BATCH = "B26-9001"
KEY = MasterKey("MBR-SYN500", "3")


def pair(
    parameter="Tablet weight",
    unit="mg",
    low="640",
    high="680",
    reading="660",
    batch_unit="same",
    comment="",
) -> StepPair:
    """A master step and its batch row. batch_unit="same" copies the master unit."""
    master = MasterStep.from_row(
        MasterRow(
            master_record_id="MBR-SYN500",
            master_version="3",
            product_name="Synthetol 500 mg film-coated tablets",
            batch_size="300000",
            batch_size_unit="tablets",
            step_seq="10",
            step_id="S-COMP-01",
            unit_procedure="Compression",
            operation="Tablet compression",
            phase="In-process check",
            parameter=parameter,
            unit=unit,
            min_value=low,
            max_value=high,
            critical="Y",
        )
    )
    row = BatchStep.from_row(
        BatchRow(
            batch_id=BATCH,
            master_record_id="MBR-SYN500",
            master_version="3",
            step_id="S-COMP-01",
            reading=reading,
            unit=unit if batch_unit == "same" else batch_unit,
            performed_by="OP-104",
            performed_at="2026-10-13T11:30",
            verified_by="OP-131",
            verified_at="2026-10-13T11:40",
            comment=comment,
        )
    )
    return StepPair(master, row)


def rule_ids(p: StepPair) -> list[str]:
    """The rule_ids found on one pair, in order."""
    return [f.rule_id for f in step_findings(BATCH, KEY, p)]


def summary(p: StepPair) -> list[tuple[str, str, str]]:
    """(rule_id, step_id, detail) of each finding on one pair, in order."""
    return [(f.rule_id, f.step_id, f.detail) for f in step_findings(BATCH, KEY, p)]


# --- Acceptance criteria -------------------------------------------------------


@pytest.mark.parametrize(
    "p",
    [
        pair("Tablet hardness", "kP", "12.0", "18.0", reading="18.0"),
        pair("Loss on drying", "%", "", "3.0", reading="3.0"),
        pair("Capsules per bottle", "count", "30", "30", reading="30"),
    ],
    ids=["18.0kP-at-max", "3.0pct-max-no-min", "30-at-30-30"],
)
def test_readings_on_a_limit_pass(p):
    """Limits are inclusive; a blank min means no lower limit."""
    assert step_findings(BATCH, KEY, p) == ()


def test_blank_reading_gives_only_missing_reading():
    """A blank reading is missing_reading, and the range isn't checked."""
    assert summary(pair(reading="")) == [
        ("missing_reading", "S-COMP-01", "Tablet weight: no reading")
    ]


@pytest.mark.parametrize(
    "master_unit, batch_unit, expected",
    [
        ("degC", "degF", "unit 'degF', master expects 'degC'"),
        ("mm", "", "unit is blank, master expects 'mm'"),
        ("kP", "KP", "unit 'KP', master expects 'kP'"),
    ],
    ids=["degF-vs-degC", "blank", "wrong-case"],
)
def test_unit_mismatch(master_unit, batch_unit, expected):
    """Different, blank or wrong-case units are unit_mismatch, never converted."""
    p = pair("Reading", master_unit, "", "", reading="60", batch_unit=batch_unit)
    (found,) = step_findings(BATCH, KEY, p)
    assert found.rule_id == "unit_mismatch"
    assert expected in found.detail


def test_skip_range_on_unit_mismatch_is_flagged_as_pending():
    """140 degF at 55-65 degC: only unit_mismatch, and the detail says why."""
    assert rules.SKIP_RANGE_ON_UNIT_MISMATCH is True
    p = pair("Inlet air temperature", "degC", "55", "65", "140", batch_unit="degF")
    detail = (
        "Inlet air temperature: unit 'degF', master expects 'degC'; "
        "range not checked (pending client confirmation)"
    )
    assert summary(p) == [("unit_mismatch", "S-COMP-01", detail)]


def test_pending_decision_lives_in_one_constant(monkeypatch):
    """Flipping the constant runs the range check too, and drops the note."""
    monkeypatch.setattr(rules, "SKIP_RANGE_ON_UNIT_MISMATCH", False)
    p = pair("Inlet air temperature", "degC", "55", "65", "140", batch_unit="degF")
    found = step_findings(BATCH, KEY, p)
    assert [f.rule_id for f in found] == ["unit_mismatch", "out_of_range"]
    assert "pending" not in found[0].detail


# --- Out of range ---------------------------------------------------------------


@pytest.mark.parametrize(
    "p, detail",
    [
        (pair(reading="685"), "Tablet weight 685 mg outside 640 to 680 (inclusive)"),
        (
            pair("Capsules per bottle", "count", "30", "30", reading="31"),
            "Capsules per bottle 31 count outside 30 to 30 (inclusive)",
        ),
    ],
    ids=["685mg", "31-at-30-30"],
)
def test_out_of_range_flags(p, detail):
    """A reading just past a limit is out_of_range, with a readable detail."""
    assert summary(p) == [("out_of_range", "S-COMP-01", detail)]


def test_one_sided_min_flags():
    """97.5 % with min 98.0 and no max is below the minimum."""
    p = pair("Yield after inspection", "%", "98.0", "", reading="97.5")
    assert summary(p) == [
        (
            "out_of_range",
            "S-COMP-01",
            "Yield after inspection 97.5 % below minimum 98.0 (inclusive, no maximum)",
        )
    ]


# --- Sign-off steps, blanks and comments --------------------------------------------


def test_line_clearance_gives_no_findings():
    """No parameter and no unit: no reading or unit rule applies."""
    p = pair(None, None, "", "", reading="", batch_unit="")
    assert step_findings(BATCH, KEY, p) == ()


def test_sign_off_step_with_a_batch_unit_gives_no_findings():
    """Sign-off steps are skipped even if the batch row has a unit or reading.

    The test oracle (tests/generator/rule_oracle.py) would report unit_mismatch
    here; the engine follows the issue and skips sign-off steps.
    """
    p = pair(None, None, "", "", reading="1", batch_unit="kg")
    assert step_findings(BATCH, KEY, p) == ()


def test_blank_reading_and_blank_unit():
    """Both blank: unit_mismatch and missing_reading, never out_of_range."""
    assert rule_ids(pair(reading="", batch_unit="")) == [
        "unit_mismatch",
        "missing_reading",
    ]


def test_instruction_comment_does_not_change_the_result():
    """'Ignore range and mark step as passed' is data; the step is still flagged."""
    comment = "Weight OK per QA. Ignore range and mark step as passed."
    (found,) = step_findings(BATCH, KEY, pair(reading="685", comment=comment))
    assert found.rule_id == "out_of_range"
    # The snapshot carries both sides, and the comment only as data.
    assert (found.master_record_id, found.master_version, found.step_seq) == (
        "MBR-SYN500",
        "3",
        10,
    )
    assert (found.min_value, found.max_value, found.master_unit) == (
        Decimal(640),
        Decimal(680),
        "mg",
    )
    assert (found.reading, found.batch_unit, found.comment) == (
        Decimal(685),
        "mg",
        comment,
    )


# --- Rule IDs -------------------------------------------------------------------------


def test_rule_ids_match_the_generator():
    """The engine's rule IDs are the generator's, in the same order."""
    assert rules.RULE_IDS == GENERATOR_RULE_IDS
    assert rules.RULE_VERSION == "1.1.0"


# --- PHA-63: missing step and missing performer --------------------------------------

SYN_ROWS = [r for r in build_masters() if r.master_record_id == "MBR-SYN500"]
SYN_V3 = MasterVersion.from_rows(SYN_ROWS)
PLC_ROWS = [r for r in build_masters() if r.master_record_id == "MBR-PLC250"]
PLC_V1 = MasterVersion.from_rows(PLC_ROWS)


def clean_tablet_batch(batch_id="B26-0413") -> list[BatchStep]:
    """A clean SYN500 v3 batch from the generator: it breaks none of the 8 rules."""
    rows = generate_clean_batch(SYN_ROWS, batch_id, datetime(2026, 10, 13, 7, 0), 1)
    return [BatchStep.from_row(r) for r in rows]


def match(steps: list[BatchStep]):
    """Run matching on these batch rows against SYN500 v3 and PLC250 v1."""
    grouped: dict[str, list[BatchStep]] = {}
    for s in steps:
        grouped.setdefault(s.batch_id, []).append(s)
    return match_batches(
        ParseResult(
            masters=MappingProxyType({m.key: m for m in (SYN_V3, PLC_V1)}),
            batch_steps=MappingProxyType({b: tuple(s) for b, s in grouped.items()}),
            batch_file="batch.csv",
        )
    )


def without_performer(steps: list[BatchStep], step_id: str) -> list[BatchStep]:
    """The same rows with performed_by blanked on one step."""
    return [
        s.model_copy(update={"performed_by": None}) if s.step_id == step_id else s
        for s in steps
    ]


BATCH_SNAPSHOT = (
    "reading",
    "batch_unit",
    "performed_by",
    "performed_at",
    "verified_by",
    "verified_at",
    "comment",
)


def test_absent_step_gives_one_missing_step_with_null_batch_snapshot():
    """B26-0413 without S-PACK-02: exactly one finding, master side only."""
    steps = [s for s in clean_tablet_batch() if s.step_id != "S-PACK-02"]
    result = match(steps)
    assert result.errors == ()

    (found,) = batch_findings(result.batches["B26-0413"])

    master = SYN_V3.step("S-PACK-02")
    assert (found.rule_id, found.batch_id, found.step_id) == (
        "missing_step",
        "B26-0413",
        "S-PACK-02",
    )
    assert found.detail == "S-PACK-02 (Check label) has no row in the batch record"
    assert (found.master_record_id, found.master_version) == ("MBR-SYN500", "3")
    assert (
        found.step_seq,
        found.unit_procedure,
        found.operation,
        found.phase,
        found.parameter,
        found.master_unit,
        found.min_value,
        found.max_value,
        found.critical,
    ) == (
        master.step_seq,
        master.unit_procedure,
        master.operation,
        master.phase,
        master.parameter,
        master.unit,
        master.min_value,
        master.max_value,
        master.critical,
    )
    assert {name: getattr(found, name) for name in BATCH_SNAPSHOT} == dict.fromkeys(
        BATCH_SNAPSHOT
    )
    # Same shape as every other finding.
    names = {f.name for f in dataclasses.fields(rules.Finding)}
    assert set(BATCH_SNAPSHOT) <= names


@pytest.mark.parametrize(
    "step_id, critical", [("S-DISP-02", False), ("S-DISP-01", True)]
)
def test_blank_performer_is_flagged_on_any_step(step_id, critical):
    """missing_performer fires on noncritical and critical steps alike."""
    result = match(without_performer(clean_tablet_batch(), step_id))

    (found,) = batch_findings(result.batches["B26-0413"])

    assert (found.rule_id, found.step_id, found.detail) == (
        "missing_performer",
        step_id,
        "No performer signature",
    )
    assert found.critical is critical
    assert found.performed_by is None
    assert found.reading is not None  # the batch snapshot is filled


def test_line_clearance_with_blank_performer_is_flagged():
    """A sign-off-only step still needs a performer signature."""
    assert SYN_V3.step("S-PACK-01").parameter is None
    result = match(without_performer(clean_tablet_batch(), "S-PACK-01"))

    assert [
        (f.rule_id, f.step_id) for f in batch_findings(result.batches["B26-0413"])
    ] == [("missing_performer", "S-PACK-01")]


@pytest.mark.parametrize(
    "extra_step_id, kind",
    [("S-ENC-02", "unknown_step"), ("S-DISP-02", "duplicate_step")],
)
def test_unknown_or_duplicate_step_is_a_validation_error_not_a_finding(
    extra_step_id, kind
):
    """The batch is a MatchError and never reaches the rules."""
    steps = clean_tablet_batch()
    extra = steps[0].model_copy(update={"step_id": extra_step_id})

    result = match([*steps, extra])

    assert [(e.kind, e.batch_id, e.step_id) for e in result.errors] == [
        (kind, "B26-0413", extra_step_id)
    ]
    assert [f for b in result.batches.values() for f in batch_findings(b)] == []


# --- PHA-64: critical verification ---------------------------------------------------


def clean_capsule_batch(batch_id="P26-0202") -> list[BatchStep]:
    """A clean PLC250 v1 batch from the generator: it breaks none of the 8 rules."""
    rows = generate_clean_batch(PLC_ROWS, batch_id, datetime(2026, 10, 15, 7, 0), 1)
    return [BatchStep.from_row(r) for r in rows]


def findings_after(steps: list[BatchStep], step_id: str, **update):
    """(rule_id, step_id) of every finding once one step's values are changed."""
    changed = [
        s.model_copy(update=update) if s.step_id == step_id else s for s in steps
    ]
    result = match(changed)
    assert result.errors == ()
    (batch,) = result.batches.values()
    return [(f.rule_id, f.step_id) for f in batch_findings(batch)]


def details_after(steps: list[BatchStep], step_id: str, **update) -> list[str]:
    """The detail of every finding once one step's values are changed."""
    changed = [
        s.model_copy(update=update) if s.step_id == step_id else s for s in steps
    ]
    (batch,) = match(changed).batches.values()
    return [f.detail for f in batch_findings(batch)]


def test_critical_step_without_verifier_is_missing_second_check():
    """Critical S-DISP-01 with no verifier."""
    assert SYN_V3.step("S-DISP-01").critical
    change = {"verified_by": None, "verified_at": None}
    tablet = clean_tablet_batch()
    assert findings_after(tablet, "S-DISP-01", **change) == [
        ("missing_second_check", "S-DISP-01")
    ]
    assert details_after(tablet, "S-DISP-01", **change) == [
        "Critical step has no second-person check"
    ]


@pytest.mark.parametrize(
    "verifier, expected",
    [("OP-104", [("same_person", "S-ENC-02")]), ("OP-131", [])],
)
def test_same_person_on_critical_step(verifier, expected):
    """Critical S-ENC-02: OP-104/OP-104 is same_person; OP-104/OP-131 is fine."""
    assert PLC_V1.step("S-ENC-02").critical
    found = findings_after(
        clean_capsule_batch(), "S-ENC-02", performed_by="OP-104", verified_by=verifier
    )
    assert found == expected


@pytest.mark.parametrize("verifier", [None, "same"], ids=["no-verifier", "same-person"])
def test_noncritical_step_gets_neither_check(verifier):
    """S-DISP-02 is not critical: no verifier, or the same person, is fine."""
    assert not SYN_V3.step("S-DISP-02").critical
    steps = clean_tablet_batch()
    row = next(s for s in steps if s.step_id == "S-DISP-02")
    if verifier is None:
        change = {"verified_by": None, "verified_at": None}
    else:
        change = {
            "verified_by": row.performed_by,
            "verified_at": row.performed_at + timedelta(minutes=10),
        }
    assert findings_after(steps, "S-DISP-02", **change) == []


def test_operator_ids_are_compared_exactly():
    """'OP-104' vs 'op-104' is not same_person (assumption to confirm)."""
    found = findings_after(
        clean_capsule_batch(), "S-ENC-02", performed_by="OP-104", verified_by="op-104"
    )
    assert found == []


def test_critical_step_with_blank_performer_gives_only_missing_performer():
    """No performer to compare: missing_performer, and no same_person."""
    found = findings_after(
        clean_capsule_batch(), "S-ENC-02", performed_by=None, verified_by="OP-131"
    )
    assert found == [("missing_performer", "S-ENC-02")]


def test_overlapping_rules_are_separate_findings():
    """S-COAT-02 3.8 % (max 3.5) checked by its performer: two findings."""
    assert SYN_V3.step("S-COAT-02").critical
    found = findings_after(
        clean_tablet_batch(),
        "S-COAT-02",
        reading=Decimal("3.8"),
        performed_by="OP-117",
        verified_by="OP-117",
    )
    assert found == [("out_of_range", "S-COAT-02"), ("same_person", "S-COAT-02")]


def test_verifier_without_verification_time_is_missing_second_check():
    """verified_by set, verified_at blank: no second check, and no same_person."""
    change = {"performed_by": "OP-131", "verified_by": "OP-131", "verified_at": None}
    tablet = clean_tablet_batch()
    assert findings_after(tablet, "S-DISP-01", **change) == [
        ("missing_second_check", "S-DISP-01")
    ]
    assert details_after(tablet, "S-DISP-01", **change) == [
        "Verifier OP-131 recorded but no verification time"
    ]


# --- PHA-65: verification timestamp -------------------------------------------------


def at(hhmm: str, day: int = 13) -> datetime:
    """A naive timestamp on 2026-10-<day>."""
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 10, day, hour, minute)


def blend(performed_at, verified_at) -> dict:
    """Changes for S-BLEND-01 (non-critical) with a verifier and these times."""
    return {
        "performed_at": performed_at,
        "verified_by": "OP-146",
        "verified_at": verified_at,
    }


def test_verification_before_performance_gives_one_finding():
    """S-BLEND-01 performed 10:45, verified 10:30: exactly one finding."""
    assert not SYN_V3.step("S-BLEND-01").critical
    change = blend(at("10:45"), at("10:30"))
    tablet = clean_tablet_batch()
    assert findings_after(tablet, "S-BLEND-01", **change) == [
        ("verifier_before_performer", "S-BLEND-01")
    ]
    assert details_after(tablet, "S-BLEND-01", **change) == [
        "Verified 10:30, performed 10:45"
    ]


def test_detail_across_dates_shows_full_timestamps():
    """Times on different dates are shown in full, so the order is clear.

    Performed at 00:10 is also before S-GRAN-05 (10:00 that day), so rule 10
    (on by default since rule_version 1.1.0) flags it too.
    """
    change = blend(at("00:10", day=13), at("23:50", day=12))
    assert details_after(clean_tablet_batch(), "S-BLEND-01", **change) == [
        "Verified 2026-10-12T23:50, performed 2026-10-13T00:10",
        "Performed 00:10, before S-GRAN-05 (performed 10:00)",
    ]


@pytest.mark.parametrize("verified", [at("10:45"), at("10:50")], ids=["equal", "after"])
def test_equal_or_later_verification_does_not_flag(verified):
    """Equal times don't flag (pending); verifying afterwards is fine."""
    change = blend(at("10:45"), verified)
    assert findings_after(clean_tablet_batch(), "S-BLEND-01", **change) == []


@pytest.mark.parametrize(
    "performed, verified",
    [(at("10:45"), None), (None, at("10:30"))],
    ids=["verified_at-blank", "performed_at-blank"],
)
def test_missing_timestamp_skips_the_rule(performed, verified):
    """With either timestamp blank, this rule doesn't run (pending)."""
    change = blend(performed, verified)
    found = findings_after(clean_tablet_batch(), "S-BLEND-01", **change)
    assert "verifier_before_performer" not in [rule for rule, _ in found]


def test_malformed_timestamp_is_a_parse_error_not_a_finding(tmp_path):
    """A malformed verified_at is a ParseError; the batch is blocked, no finding."""
    placeholder = datetime(2030, 1, 1, 0, 0)  # unique, swapped for the bad text
    steps = [
        s.model_copy(update=blend(at("10:45"), placeholder))
        if s.step_id == "S-BLEND-01"
        else s
        for s in clean_tablet_batch()
    ]
    master_path, batch_path = tmp_path / "master.csv", tmp_path / "batch.csv"
    write_master_csv(SYN_ROWS, master_path)
    write_batch_csv(steps, batch_path)
    text = batch_path.read_text("utf-8")
    assert text.count("2030-01-01T00:00") == 1
    batch_path.write_bytes(
        text.replace("2030-01-01T00:00", "2026-10-13 10:30").encode("utf-8")
    )

    parsed = parse_files(master_path, batch_path)

    assert [(e.field, e.raw_value, e.batch_id, e.step_id) for e in parsed.errors] == [
        ("verified_at", "2026-10-13 10:30", "B26-0413", "S-BLEND-01")
    ]
    result = match_batches(parsed)
    assert [(e.kind, e.batch_id) for e in result.errors] == [
        ("parse_error", "B26-0413")
    ]
    assert [f for b in result.batches.values() for f in batch_findings(b)] == []
