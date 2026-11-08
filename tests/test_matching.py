"""Tests for matching batches to master steps (PHA-61). All data is synthetic."""

from decimal import Decimal
from types import MappingProxyType
from typing import get_args

from pharma_batch_record_checker.generator.expected_findings import RULE_IDS
from pharma_batch_record_checker.matching import (
    MatchErrorKind,
    StepPair,
    match_batches,
)
from pharma_batch_record_checker.parser import ParseError, ParseResult
from pharma_batch_record_checker.records import BatchStep, MasterVersion
from pharma_batch_record_checker.schema import BatchRow, MasterRow

SYN = ("MBR-SYN500", "3", "Synthetol 500 mg film-coated tablets", "tablets")
PLC = ("MBR-PLC250", "1", "Placeprol 250 mg hard capsules", "capsules")


def master(product, *steps) -> MasterVersion:
    """A master version from (step_seq, step_id, min, max) tuples."""
    master_id, version, name, size_unit = product
    return MasterVersion.from_rows(
        MasterRow(
            master_record_id=master_id,
            master_version=version,
            product_name=name,
            batch_size="100000",
            batch_size_unit=size_unit,
            step_seq=seq,
            step_id=step_id,
            unit_procedure="Dispensing",
            operation="Weigh raw materials",
            phase="Weigh",
            parameter="Net weight",
            unit="kg",
            min_value=low,
            max_value=high,
            critical="Y",
        )
        for seq, step_id, low, high in steps
    )


SYN_V3 = master(
    SYN,
    (1, "S-DISP-01", "149.25", "150.75"),
    (2, "S-DISP-02", "29.85", "30.15"),
    (10, "S-COMP-01", "640", "680"),
)
PLC_V1 = master(PLC, (1, "S-DISP-01", "49.75", "50.25"))
MASTERS = MappingProxyType({m.key: m for m in (SYN_V3, PLC_V1)})


def step(batch_id, step_id, product=SYN, reading="1", version=None) -> BatchStep:
    """A valid batch row for one step."""
    return BatchStep.from_row(
        BatchRow(
            batch_id=batch_id,
            master_record_id=product[0],
            master_version=version or product[1],
            step_id=step_id,
            reading=reading,
            unit="kg",
            performed_by="OP-104",
            performed_at="2026-10-13T07:00",
            verified_by="OP-131",
            verified_at="2026-10-13T07:10",
            comment="",
        )
    )


def parsed(*steps, errors=(), batch_file="batch.csv") -> ParseResult:
    """A ParseResult holding the test masters and these batch rows."""
    grouped: dict[str, list[BatchStep]] = {}
    for s in steps:
        grouped.setdefault(s.batch_id, []).append(s)
    return ParseResult(
        masters=MASTERS,
        batch_steps=MappingProxyType({b: tuple(s) for b, s in grouped.items()}),
        errors=tuple(errors),
        batch_file=batch_file,
    )


def full_syn(batch_id):
    """Every SYN500 v3 step for one batch."""
    return [step(batch_id, s.step_id) for s in SYN_V3.steps]


# --- Acceptance criteria -------------------------------------------------------


def test_shared_step_ids_across_products_do_not_collide():
    """S-DISP-01 in a tablet and a capsule batch pairs with its own master step."""
    tablet = step("B26-9001", "S-DISP-01", SYN, reading="150.02")
    capsule = step("P26-9001", "S-DISP-01", PLC, reading="50.01")
    rest = [step("B26-9001", "S-DISP-02"), step("B26-9001", "S-COMP-01")]

    result = match_batches(parsed(tablet, capsule, *rest))

    assert result.errors == ()
    syn_pair = result.batches["B26-9001"].pairs[0]
    plc_pair = result.batches["P26-9001"].pairs[0]
    assert syn_pair == StepPair(SYN_V3.step("S-DISP-01"), tablet)
    assert plc_pair == StepPair(PLC_V1.step("S-DISP-01"), capsule)
    assert (syn_pair.master.min_value, plc_pair.master.min_value) == (
        Decimal("149.25"),
        Decimal("49.75"),
    )


def test_missing_master_step_is_passed_to_rule_layer():
    """An absent step is a (master step, None) pair, in step_seq order."""
    # Rows out of order, and S-DISP-02 missing.
    comp, disp = step("B26-9001", "S-COMP-01"), step("B26-9001", "S-DISP-01")

    result = match_batches(parsed(comp, disp))

    assert result.errors == ()
    batch = result.batches["B26-9001"]
    assert batch.master is SYN_V3
    assert batch.pairs == (
        StepPair(SYN_V3.step("S-DISP-01"), disp),
        StepPair(SYN_V3.step("S-DISP-02"), None),
        StepPair(SYN_V3.step("S-COMP-01"), comp),
    )
    assert batch.missing_steps == (SYN_V3.step("S-DISP-02"),)


def test_unknown_step_id_is_an_error_not_a_finding():
    """A step_id not in the batch's master version is a validation error."""
    rows = [*full_syn("B26-9001"), step("B26-9001", "S-ENC-02")]

    result = match_batches(parsed(*rows, *full_syn("B26-9002")))

    assert [(e.kind, e.batch_id, e.step_id) for e in result.errors] == [
        ("unknown_step", "B26-9001", "S-ENC-02")
    ]
    assert "S-ENC-02 is not in MBR-SYN500 v3" in result.errors[0].reason
    assert list(result.batches) == ["B26-9002"]  # the bad batch isn't matched
    assert not set(get_args(MatchErrorKind)) & set(RULE_IDS)  # no rule IDs


def test_duplicate_step_id_is_an_error_not_a_finding():
    """A step_id appearing twice in one batch is a validation error."""
    rows = [*full_syn("B26-9001"), step("B26-9001", "S-DISP-02")]

    result = match_batches(parsed(*rows))

    assert [(e.kind, e.batch_id, e.step_id) for e in result.errors] == [
        ("duplicate_step", "B26-9001", "S-DISP-02")
    ]
    assert "appears 2 times" in result.errors[0].reason
    assert dict(result.batches) == {}


# --- Other validation errors -----------------------------------------------------


def test_unknown_master_version_is_an_error():
    """A batch naming a master version that isn't loaded is not matched."""
    rows = [step("B26-9001", s.step_id, version="4") for s in SYN_V3.steps]

    result = match_batches(parsed(*rows))

    assert [(e.kind, e.batch_id) for e in result.errors] == [
        ("unknown_master", "B26-9001")
    ]
    assert "no loaded master MBR-SYN500 v4" in result.errors[0].reason
    assert dict(result.batches) == {}


def test_batch_naming_two_masters_is_an_error():
    """A batch whose rows name different master versions is not matched."""
    rows = [*full_syn("B26-9001"), step("B26-9001", "S-DISP-01", PLC)]

    result = match_batches(parsed(*rows))

    assert [(e.kind, e.batch_id) for e in result.errors] == [
        ("mixed_master", "B26-9001")
    ]
    assert "MBR-SYN500 v3, MBR-PLC250 v1" in result.errors[0].reason
    assert dict(result.batches) == {}


def test_batch_with_a_parse_error_is_not_matched():
    """A ParseError tied to a batch blocks that batch only.

    A master-file error (no batch_id, other file) doesn't block any batch.
    """
    bad_row = ParseError(
        file="batch.csv",
        row=3,
        field="reading",
        reason="number must be plain notation",
        raw_value="thirty",
        batch_id="B26-9001",
        step_id="S-DISP-02",
    )
    master_error = ParseError(
        file="master.csv", row=None, field=None, reason="master X v1 not loaded"
    )
    rows = [s for s in full_syn("B26-9001") if s.step_id != "S-DISP-02"]

    result = match_batches(
        parsed(*rows, *full_syn("B26-9002"), errors=[master_error, bad_row])
    )

    assert [(e.kind, e.batch_id) for e in result.errors] == [
        ("parse_error", "B26-9001")
    ]
    assert result.errors[0].reason == "batch B26-9001 not matched: 1 invalid row"
    assert list(result.batches) == ["B26-9002"]


def test_unattributed_batch_file_error_blocks_every_batch():
    """A batch-file error with no batch_id could hide any batch's step."""
    blank_line = ParseError(
        file="batch.csv", row=5, field=None, reason="blank line between rows"
    )

    result = match_batches(
        parsed(*full_syn("B26-9001"), *full_syn("B26-9002"), errors=[blank_line])
    )

    assert [(e.kind, e.batch_id) for e in result.errors] == [("parse_error", None)]
    assert "1 parse error in the batch file" in result.errors[0].reason
    assert dict(result.batches) == {}
