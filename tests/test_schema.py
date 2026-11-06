"""Tests for the master/batch CSV row models (PHA-33). All data is synthetic."""

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
