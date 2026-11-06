"""Tests for the master/batch CSV row models (PHA-33). All data is synthetic."""

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from pharma_batch_record_checker.schema import (
    BATCH_HEADER,
    MASTER_HEADER,
    BatchRow,
    MasterRow,
)

# Exact client-approved headers, copied verbatim from the issue.
CLIENT_MASTER_HEADER = (
    "master_record_id,master_version,product_name,batch_size,batch_size_unit,"
    "step_seq,step_id,unit_procedure,operation,phase,parameter,unit,"
    "min_value,max_value,critical"
)
CLIENT_BATCH_HEADER = (
    "batch_id,master_record_id,master_version,step_id,reading,unit,"
    "performed_by,performed_at,verified_by,verified_at,comment"
)


# --- Slice 1: headers -------------------------------------------------------


def test_master_header_matches_client_order():
    assert ",".join(MASTER_HEADER) == CLIENT_MASTER_HEADER


def test_batch_header_matches_client_order():
    assert ",".join(BATCH_HEADER) == CLIENT_BATCH_HEADER


def test_master_model_fields_follow_header():
    assert tuple(MasterRow.model_fields) == MASTER_HEADER


def test_batch_model_fields_follow_header():
    assert tuple(BatchRow.model_fields) == BATCH_HEADER


# --- Slice 2: MasterRow validation ------------------------------------------


def master_data(**overrides):
    """A synthetic measured, critical step with both limits set."""
    data = {
        "master_record_id": "MR-TAB-001",
        "master_version": "1",
        "product_name": "Synthetamol 500 mg Tablets",
        "batch_size": "100000",
        "batch_size_unit": "tablets",
        "step_seq": "1",
        "step_id": "S010",
        "unit_procedure": "Granulation",
        "operation": "Wet mixing",
        "phase": "Mix",
        "parameter": "Mixing time",
        "unit": "min",
        "min_value": "10",
        "max_value": "15",
        "critical": "Y",
    }
    data.update(overrides)
    return data


def sign_off_data(**overrides):
    """A synthetic sign-off-only step: no parameter, unit or limits."""
    return master_data(parameter="", unit="", min_value="", max_value="", **overrides)


def test_master_measured_step_validates():
    row = MasterRow.model_validate(master_data())
    assert row.min_value == Decimal(10)
    assert row.max_value == Decimal(15)
    assert row.step_seq == 1


def test_master_sign_off_only_step_validates():
    row = MasterRow.model_validate(sign_off_data())
    assert row.parameter is None
    assert row.unit is None
    assert row.min_value is None
    assert row.max_value is None


def test_master_one_sided_limit_validates():
    row = MasterRow.model_validate(master_data(min_value="5", max_value=""))
    assert row.min_value == Decimal(5)
    assert row.max_value is None


def test_master_equal_limits_allowed():
    row = MasterRow.model_validate(master_data(min_value="7", max_value="7"))
    assert row.min_value == row.max_value


def test_master_min_greater_than_max_rejected():
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(min_value="16", max_value="15"))


def test_master_sign_off_step_with_unit_rejected():
    with pytest.raises(ValidationError):
        MasterRow.model_validate(sign_off_data() | {"unit": "min"})


def test_master_sign_off_step_with_limit_rejected():
    with pytest.raises(ValidationError):
        MasterRow.model_validate(sign_off_data() | {"min_value": "1"})


@pytest.mark.parametrize(("raw", "expected"), [("Y", True), ("N", False)])
def test_master_critical_accepts_y_n(raw, expected):
    assert MasterRow.model_validate(master_data(critical=raw)).critical is expected


@pytest.mark.parametrize("raw", ["yes", "y", "1", ""])
def test_master_critical_rejects_other_values(raw):
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(critical=raw))


def test_master_required_field_blank_rejected():
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(product_name=""))


def test_master_step_seq_must_be_positive():
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(step_seq="0"))


def test_master_batch_size_must_be_positive():
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(batch_size="0"))


def test_master_extra_field_rejected():
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(notes="unexpected"))


# --- Slice 3: BatchRow validation -------------------------------------------


def batch_data(**overrides):
    """A synthetic, fully signed-off executed step."""
    data = {
        "batch_id": "B-TAB-0001",
        "master_record_id": "MR-TAB-001",
        "master_version": "1",
        "step_id": "S010",
        "reading": "12.5",
        "unit": "min",
        "performed_by": "OP-07",
        "performed_at": "2026-01-15T08:30:00",
        "verified_by": "QA-02",
        "verified_at": "2026-01-15T08:45:00",
        "comment": "",
    }
    data.update(overrides)
    return data


BATCH_OPTIONAL_FIELDS = (
    "reading",
    "unit",
    "performed_by",
    "performed_at",
    "verified_by",
    "verified_at",
    "comment",
)
BATCH_KEY_FIELDS = ("batch_id", "master_record_id", "master_version", "step_id")


def test_batch_clean_row_validates():
    row = BatchRow.model_validate(batch_data())
    assert row.reading == Decimal("12.5")
    assert row.performed_at == datetime(2026, 1, 15, 8, 30)
    assert row.comment is None


def test_batch_blank_optional_fields_validate():
    row = BatchRow.model_validate(
        batch_data(**dict.fromkeys(BATCH_OPTIONAL_FIELDS, ""))
    )
    for field in BATCH_OPTIONAL_FIELDS:
        assert getattr(row, field) is None


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"reading": "99"}, id="out_of_range"),
        pytest.param({"performed_by": "", "performed_at": ""}, id="missing_performer"),
        pytest.param(
            {"verified_at": "2026-01-15T08:00:00"}, id="verifier_before_performer"
        ),
        pytest.param({"unit": "MIN"}, id="unit_mismatch"),
    ],
)
def test_batch_defect_rows_validate(overrides):
    # Planted defects must be representable; finding them is the rule engine's job.
    row = BatchRow.model_validate(batch_data(**overrides))
    if "unit" in overrides:
        assert row.unit == "MIN"  # kept exactly as written, never normalized


@pytest.mark.parametrize("field", BATCH_KEY_FIELDS)
def test_batch_key_field_blank_rejected(field):
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(**{field: ""}))


def test_batch_timezone_aware_timestamp_rejected():
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(performed_at="2026-01-15T08:30:00+05:30"))


def test_batch_reading_must_be_numeric_when_present():
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(reading="abc"))


# --- Slice 4: row serialization ---------------------------------------------


def test_master_to_csv_row_keys_in_header_order():
    assert tuple(MasterRow.model_validate(master_data()).to_csv_row()) == MASTER_HEADER


def test_master_to_csv_row_blank_fields_are_empty_strings():
    out = MasterRow.model_validate(sign_off_data()).to_csv_row()
    assert [out[f] for f in ("parameter", "unit", "min_value", "max_value")] == [
        "",
        "",
        "",
        "",
    ]


@pytest.mark.parametrize("flag", ["Y", "N"])
def test_master_to_csv_row_critical_is_y_or_n(flag):
    assert (
        MasterRow.model_validate(master_data(critical=flag)).to_csv_row()["critical"]
        == flag
    )


def test_decimal_kept_as_written():
    out = MasterRow.model_validate(
        master_data(min_value="7.50", max_value="100", batch_size="250000")
    ).to_csv_row()
    assert (out["min_value"], out["max_value"], out["batch_size"]) == (
        "7.50",
        "100",
        "250000",
    )


def test_batch_to_csv_row_timestamp_format():
    out = BatchRow.model_validate(
        batch_data(performed_at=datetime(2026, 1, 15, 8, 30, 0, 123456))
    ).to_csv_row()
    # ISO 8601, no timezone, whole seconds.
    assert out["performed_at"] == "2026-01-15T08:30:00"
    assert out["verified_at"] == "2026-01-15T08:45:00"


def test_batch_to_csv_row_blank_fields_are_empty_strings():
    out = BatchRow.model_validate(
        batch_data(**dict.fromkeys(BATCH_OPTIONAL_FIELDS, ""))
    ).to_csv_row()
    assert tuple(out) == BATCH_HEADER
    assert all(out[f] == "" for f in BATCH_OPTIONAL_FIELDS)
