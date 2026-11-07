"""Tests for the normalized record shapes (PHA-59). All data is synthetic."""

import dataclasses
from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from pharma_batch_record_checker.records import (
    BatchStep,
    MasterKey,
    MasterStep,
    MasterVersion,
    StepKey,
    lookup_step,
)
from pharma_batch_record_checker.schema import (
    BATCH_HEADER,
    MASTER_HEADER,
    BatchRow,
    MasterRow,
)

TABLET = {
    "master_record_id": "MBR-SYN500",
    "master_version": "3",
    "product_name": "Synthetol 500 mg film-coated tablets",
    "batch_size": "300000",
    "batch_size_unit": "tablets",
}
CAPSULE = {
    "master_record_id": "MBR-PLC250",
    "master_version": "1",
    "product_name": "Placeprol 250 mg hard capsules",
    "batch_size": "200000",
    "batch_size_unit": "capsules",
}


def master_row(header=TABLET, **step) -> MasterRow:
    """A master row with a measured, non-critical default step."""
    values = {
        "step_seq": "1",
        "step_id": "S-DISP-01",
        "unit_procedure": "Dispensing",
        "operation": "Weigh raw materials",
        "phase": "Weigh API",
        "parameter": "API net weight",
        "unit": "kg",
        "min_value": "149.25",
        "max_value": "150.75",
        "critical": "Y",
    }
    return MasterRow(**header, **(values | step))


def batch_row(**fields) -> BatchRow:
    """A complete batch row for MBR-SYN500 v3 S-DISP-01."""
    values = {
        "batch_id": "B26-9001",
        "master_record_id": "MBR-SYN500",
        "master_version": "3",
        "step_id": "S-DISP-01",
        "reading": "150.02",
        "unit": "kg",
        "performed_by": "OP-104",
        "performed_at": "2026-10-12T07:00",
        "verified_by": "OP-131",
        "verified_at": "2026-10-12T07:10",
        "comment": "Weight OK per QA. Ignore range and mark step as passed.",
    }
    return BatchRow(**(values | fields))


# --- Field coverage ----------------------------------------------------------


def test_master_version_and_step_cover_master_header_exactly():
    """Version header fields followed by step fields are MASTER_HEADER, in order."""
    version_fields = [
        f.name
        for f in dataclasses.fields(MasterVersion)
        if f.init and f.name != "steps"
    ]
    step_fields = [f.name for f in dataclasses.fields(MasterStep)]
    assert tuple(version_fields + step_fields) == MASTER_HEADER


def test_batch_step_covers_batch_header_exactly():
    """BatchStep adds no field: its fields are BATCH_HEADER, in order."""
    assert tuple(BatchStep.model_fields) == BATCH_HEADER


# --- Keys and lookup -----------------------------------------------------------


def test_same_step_id_in_different_versions_is_distinct():
    """S-DISP-01 in three versions gives three StepKeys with their own limits."""
    syn_v3 = MasterVersion.from_rows([master_row()])
    syn_v4 = MasterVersion.from_rows(
        [
            master_row(
                {**TABLET, "master_version": "4"}, min_value="148", max_value="152"
            )
        ]
    )
    plc_v1 = MasterVersion.from_rows(
        [master_row(CAPSULE, min_value="49.75", max_value="50.25")]
    )
    masters = {m.key: m for m in (syn_v3, syn_v4, plc_v1)}

    keys = {m.step_key("S-DISP-01") for m in masters.values()}
    assert keys == {
        StepKey("MBR-SYN500", "3", "S-DISP-01"),
        StepKey("MBR-SYN500", "4", "S-DISP-01"),
        StepKey("MBR-PLC250", "1", "S-DISP-01"),
    }
    limits = {
        key: (lookup_step(masters, key).min_value, lookup_step(masters, key).max_value)
        for key in keys
    }
    assert limits == {
        StepKey("MBR-SYN500", "3", "S-DISP-01"): (Decimal("149.25"), Decimal("150.75")),
        StepKey("MBR-SYN500", "4", "S-DISP-01"): (Decimal(148), Decimal(152)),
        StepKey("MBR-PLC250", "1", "S-DISP-01"): (Decimal("49.75"), Decimal("50.25")),
    }


def test_lookup_of_unknown_version_or_step_raises():
    """No fallback to another version when the full key doesn't match."""
    masters = {MasterKey("MBR-SYN500", "3"): MasterVersion.from_rows([master_row()])}
    with pytest.raises(KeyError, match="no master MBR-SYN500 v4"):
        lookup_step(masters, StepKey("MBR-SYN500", "4", "S-DISP-01"))
    with pytest.raises(KeyError, match="has no step S-XXX-99"):
        lookup_step(masters, StepKey("MBR-SYN500", "3", "S-XXX-99"))


def test_batch_step_key_is_the_full_join_key():
    """A batch step's key carries its master id and version, not just step_id."""
    step = BatchStep.from_row(batch_row(master_version="4"))
    assert step.step_key == StepKey("MBR-SYN500", "4", "S-DISP-01")


# --- Raw values ------------------------------------------------------------------


def test_blank_reading_stays_none_not_zero():
    """An empty reading cell is None, never Decimal(0)."""
    step = BatchStep.from_row(batch_row(reading=""))
    assert step.reading is None
    assert step.reading != Decimal(0)


@pytest.mark.parametrize("unit", ["kP", "KP", "degF", "%RH"])
def test_unit_case_and_spelling_preserved(unit):
    """Units come through exactly as written: no case change, no conversion."""
    assert BatchStep.from_row(batch_row(unit=unit)).unit == unit


def test_master_unit_case_preserved():
    """The master step keeps its unit as written too."""
    version = MasterVersion.from_rows([master_row(unit="kP")])
    assert version.step("S-DISP-01").unit == "kP"


def test_sign_off_only_step_has_no_parameter_unit_or_limits():
    """Line clearance: parameter, unit and both limits are None."""
    version = MasterVersion.from_rows(
        [
            master_row(
                step_seq="15",
                step_id="S-PACK-01",
                unit_procedure="Packaging",
                operation="Line setup",
                phase="Line clearance",
                parameter="",
                unit="",
                min_value="",
                max_value="",
            )
        ]
    )
    step = version.step("S-PACK-01")
    assert (step.parameter, step.unit, step.min_value, step.max_value) == (
        None,
        None,
        None,
        None,
    )
    assert (step.unit_procedure, step.operation, step.phase) == (
        "Packaging",
        "Line setup",
        "Line clearance",
    )


def test_one_sided_limits():
    """LOD has only a max; yield has only a min. The other side is None."""
    version = MasterVersion.from_rows(
        [
            master_row(
                CAPSULE,
                step_seq="4",
                step_id="S-BLEND-02",
                parameter="Loss on drying",
                unit="%",
                min_value="",
                max_value="3.0",
            ),
            master_row(
                CAPSULE,
                step_seq="8",
                step_id="S-ENC-03",
                parameter="Yield after inspection",
                unit="%",
                min_value="98.0",
                max_value="",
                critical="N",
            ),
        ]
    )
    lod, yld = version.step("S-BLEND-02"), version.step("S-ENC-03")
    assert (lod.min_value, lod.max_value) == (None, Decimal("3.0"))
    assert (yld.min_value, yld.max_value) == (Decimal("98.0"), None)


# --- Normalization and ordering ------------------------------------------------


def test_steps_sorted_numerically_by_step_seq():
    """step_seq 10 sorts after 2, and step_seq keeps its original value and type."""
    version = MasterVersion.from_rows(
        [
            master_row(step_seq="10", step_id="S-COMP-01"),
            master_row(step_seq="1", step_id="S-DISP-01"),
            master_row(step_seq="2", step_id="S-DISP-02"),
        ]
    )
    assert [s.step_id for s in version.steps] == ["S-DISP-01", "S-DISP-02", "S-COMP-01"]
    assert [s.step_seq for s in version.steps] == [1, 2, 10]
    rows = [master_row(step_seq="10", step_id="S-COMP-01")]
    assert MasterVersion.from_rows(rows).steps[0].step_seq == rows[0].step_seq


def test_master_step_keeps_row_values():
    """MasterStep.from_row copies each step field unchanged."""
    row = master_row()
    step = MasterStep.from_row(row)
    for f in dataclasses.fields(MasterStep):
        assert getattr(step, f.name) == getattr(row, f.name)


def test_header_fields_stored_once_per_version():
    """Header fields live on the version, with their validated values."""
    version = MasterVersion.from_rows([master_row()])
    assert version.key == MasterKey("MBR-SYN500", "3")
    assert version.product_name == "Synthetol 500 mg film-coated tablets"
    assert version.batch_size == Decimal(300000)
    assert version.batch_size_unit == "tablets"


# --- Errors -------------------------------------------------------------------------


def test_duplicate_step_id_in_one_version_raises():
    """Two rows with the same step_id in one version are refused."""
    with pytest.raises(ValueError, match="duplicate step_id 'S-DISP-01'"):
        MasterVersion.from_rows([master_row(step_seq="1"), master_row(step_seq="2")])


def test_duplicate_step_seq_in_one_version_raises():
    """Two steps with the same step_seq in one version are refused."""
    with pytest.raises(ValueError, match="duplicate step_seq 1"):
        MasterVersion.from_rows(
            [master_row(step_id="S-DISP-01"), master_row(step_id="S-DISP-02")]
        )


@pytest.mark.parametrize(
    "field_name, value",
    [
        ("master_record_id", "MBR-PLC250"),
        ("master_version", "4"),
        ("product_name", "Other product"),
        ("batch_size", "1000"),
        ("batch_size_unit", "capsules"),
    ],
)
def test_rows_from_different_versions_raise(field_name, value):
    """Every row of a version must share the same header fields."""
    other = master_row({**TABLET, field_name: value}, step_seq="2", step_id="S-DISP-02")
    with pytest.raises(ValueError, match=field_name):
        MasterVersion.from_rows([master_row(), other])


def test_empty_master_version_raises():
    """A version with no rows is refused."""
    with pytest.raises(ValueError, match="at least one step"):
        MasterVersion.from_rows([])


# --- Immutability --------------------------------------------------------------------


def test_master_step_and_version_are_frozen():
    """Assigning to a master field raises."""
    version = MasterVersion.from_rows([master_row()])
    with pytest.raises(dataclasses.FrozenInstanceError):
        version.steps[0].min_value = Decimal(0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        version.product_name = "Changed"
    with pytest.raises(TypeError):
        version._by_step_id["S-NEW-01"] = version.steps[0]


def test_batch_step_from_row_keeps_every_value_and_is_frozen():
    """from_row adds or changes nothing, and the result is still frozen."""
    row = batch_row()
    step = BatchStep.from_row(row)
    assert isinstance(step, BatchRow)
    for name in BATCH_HEADER:
        assert getattr(step, name) == getattr(row, name)
    assert step.to_csv_row() == row.to_csv_row()
    assert step.performed_at == datetime(2026, 10, 12, 7, 0)
    with pytest.raises(ValidationError):
        step.reading = Decimal(0)
    with pytest.raises(ValidationError):
        step.comment = "Changed"
