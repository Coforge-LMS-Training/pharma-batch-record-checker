"""Tests for the master/batch CSV row models (PHA-33). All data is synthetic."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

import pharma_batch_record_checker.schema as schema_module
from pharma_batch_record_checker.schema import (
    BATCH_HEADER,
    MASTER_HEADER,
    BatchRow,
    CsvRow,
    MasterRow,
    Text,
    check_header,
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
    """Master header is the client-approved string, column for column."""
    assert ",".join(MASTER_HEADER) == CLIENT_MASTER_HEADER


def test_batch_header_matches_client_order():
    """Batch header is the client-approved string, column for column."""
    assert ",".join(BATCH_HEADER) == CLIENT_BATCH_HEADER


def test_master_model_fields_follow_header():
    """MasterRow declares its fields in master header order."""
    assert tuple(MasterRow.model_fields) == MASTER_HEADER


def test_batch_model_fields_follow_header():
    """BatchRow declares its fields in batch header order."""
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
    """A measured step with both limits parses into typed values."""
    row = MasterRow.model_validate(master_data())
    assert row.min_value == Decimal(10)
    assert row.max_value == Decimal(15)
    assert row.step_seq == 1


def test_master_sign_off_only_step_validates():
    """Blank parameter, unit and limits become None for a sign-off step."""
    row = MasterRow.model_validate(sign_off_data())
    assert row.parameter is None
    assert row.unit is None
    assert row.min_value is None
    assert row.max_value is None


def test_master_one_sided_limit_validates():
    """A blank max means unbounded above; only the min is set."""
    row = MasterRow.model_validate(master_data(min_value="5", max_value=""))
    assert row.min_value == Decimal(5)
    assert row.max_value is None


def test_master_equal_limits_allowed():
    """Limits are inclusive, so min == max is valid."""
    row = MasterRow.model_validate(master_data(min_value="7", max_value="7"))
    assert row.min_value == row.max_value


def test_master_min_greater_than_max_rejected():
    """min_value above max_value is an invalid master row."""
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(min_value="16", max_value="15"))


def test_master_sign_off_step_with_unit_rejected():
    """A sign-off step (blank parameter) must not carry a unit."""
    with pytest.raises(ValidationError):
        MasterRow.model_validate(sign_off_data() | {"unit": "min"})


def test_master_sign_off_step_with_limit_rejected():
    """A sign-off step (blank parameter) must not carry a limit."""
    with pytest.raises(ValidationError):
        MasterRow.model_validate(sign_off_data() | {"min_value": "1"})


@pytest.mark.parametrize(("raw", "expected"), [("Y", True), ("N", False)])
def test_master_critical_accepts_y_n(raw, expected):
    """critical parses Y as True and N as False."""
    assert MasterRow.model_validate(master_data(critical=raw)).critical is expected


@pytest.mark.parametrize("raw", ["yes", "y", "1", ""])
def test_master_critical_rejects_other_values(raw):
    """critical rejects anything other than exactly Y or N."""
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(critical=raw))


def test_master_required_field_blank_rejected():
    """Required text fields may not be blank."""
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(product_name=""))


def test_master_step_seq_must_be_positive():
    """step_seq starts at 1."""
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(step_seq="0"))


def test_master_batch_size_must_be_positive():
    """batch_size must be greater than zero."""
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(batch_size="0"))


def test_master_extra_field_rejected():
    """Columns outside the contract are rejected."""
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
        "performed_at": "2026-01-15T08:30",
        "verified_by": "QA-02",
        "verified_at": "2026-01-15T08:45",
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
    """A fully signed-off row parses reading, timestamps and a blank comment."""
    row = BatchRow.model_validate(batch_data())
    assert row.reading == Decimal("12.5")
    assert row.performed_at == datetime(2026, 1, 15, 8, 30)
    assert row.comment is None


def test_batch_blank_optional_fields_validate():
    """Every non-key field may be blank and becomes None."""
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
            {"verified_at": "2026-01-15T08:00"}, id="verifier_before_performer"
        ),
        pytest.param({"unit": "MIN"}, id="unit_mismatch"),
    ],
)
def test_batch_defect_rows_validate(overrides):
    """Planted defects stay representable; detecting them is the rule engine's job."""
    row = BatchRow.model_validate(batch_data(**overrides))
    if "unit" in overrides:
        assert row.unit == "MIN"  # kept exactly as written, never normalized


@pytest.mark.parametrize("field", BATCH_KEY_FIELDS)
def test_batch_key_field_blank_rejected(field):
    """Each part of the join key is required."""
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(**{field: ""}))


def test_batch_timezone_aware_timestamp_rejected():
    """Timestamps have no timezone; an offset is rejected."""
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(performed_at="2026-01-15T08:30+05:30"))


def test_batch_reading_must_be_numeric_when_present():
    """A non-blank reading must be a number."""
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(reading="abc"))


# --- Slice 4: row serialization ---------------------------------------------


def test_master_to_csv_row_keys_in_header_order():
    """Serialized master row keys follow the header order."""
    assert tuple(MasterRow.model_validate(master_data()).to_csv_row()) == MASTER_HEADER


def test_master_to_csv_row_blank_fields_are_empty_strings():
    """None fields serialize as empty cells, never 0."""
    out = MasterRow.model_validate(sign_off_data()).to_csv_row()
    assert [out[f] for f in ("parameter", "unit", "min_value", "max_value")] == [
        "",
        "",
        "",
        "",
    ]


@pytest.mark.parametrize("flag", ["Y", "N"])
def test_master_to_csv_row_critical_is_y_or_n(flag):
    """critical serializes back to exactly Y or N."""
    assert (
        MasterRow.model_validate(master_data(critical=flag)).to_csv_row()["critical"]
        == flag
    )


def test_decimal_kept_as_written():
    """Decimals keep their written form, including trailing zeros."""
    out = MasterRow.model_validate(
        master_data(min_value="7.50", max_value="100", batch_size="250000")
    ).to_csv_row()
    assert (out["min_value"], out["max_value"], out["batch_size"]) == (
        "7.50",
        "100",
        "250000",
    )


def test_batch_to_csv_row_timestamp_format():
    """Timestamps serialize as ISO 8601 to the minute, with no timezone."""
    out = BatchRow.model_validate(
        batch_data(performed_at=datetime(2026, 1, 15, 8, 30))
    ).to_csv_row()
    assert out["performed_at"] == "2026-01-15T08:30"
    assert out["verified_at"] == "2026-01-15T08:45"


def test_batch_to_csv_row_blank_fields_are_empty_strings():
    """Blank batch fields serialize as empty cells, in header order."""
    out = BatchRow.model_validate(
        batch_data(**dict.fromkeys(BATCH_OPTIONAL_FIELDS, ""))
    ).to_csv_row()
    assert tuple(out) == BATCH_HEADER
    assert all(out[f] == "" for f in BATCH_OPTIONAL_FIELDS)


# --- Review fix A: strict timestamps ----------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "2026-01-15T08:30:00",
        "2026-01-15T08:30:00.999",
        "2026-01-15",
        "2026-01-15 08:30:00",
        "2026-1-15T8:30:00",
        " 2026-01-15T08:30",
    ],
)
def test_batch_timestamp_rejects_non_canonical_strings(raw):
    """Only YYYY-MM-DDTHH:MM is accepted, so nothing is rewritten on output."""
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(performed_at=raw))


def test_batch_timestamp_rejects_datetime_with_microseconds():
    """A datetime with microseconds would be truncated on output, so it is rejected."""
    with pytest.raises(ValidationError):
        BatchRow.model_validate(
            batch_data(performed_at=datetime(2026, 1, 15, 8, 30, 0, 123456))
        )


def test_batch_timestamp_rejects_datetime_with_seconds():
    """Timestamps are minute precision; seconds would be lost on output."""
    with pytest.raises(ValidationError):
        BatchRow.model_validate(
            batch_data(performed_at=datetime(2026, 1, 15, 8, 30, 5))
        )


def test_batch_timestamp_rejects_date_object():
    """A bare date must not become a made-up midnight timestamp."""
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(performed_at=date(2026, 1, 15)))


def test_batch_timestamp_string_round_trips_as_written():
    """A canonical timestamp string is written back unchanged."""
    raw = "2026-12-31T23:59"
    assert (
        BatchRow.model_validate(batch_data(verified_at=raw)).to_csv_row()["verified_at"]
        == raw
    )


# --- Review fix B: plain-notation decimals ----------------------------------


@pytest.mark.parametrize(
    "raw", ["1e2", " 12.5 ", "+5", "NaN", "Infinity", "1_000", ".5", "5."]
)
def test_decimal_rejects_non_plain_notation(raw):
    """Only plain notation is accepted, so a number is written back as given."""
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(reading=raw))


def test_master_limit_rejects_exponent():
    """Limits follow the same plain-notation rule as readings."""
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(min_value="1e1"))


@pytest.mark.parametrize("raw", [12.5, True])
def test_decimal_rejects_float_and_bool(raw):
    """Floats (inexact) and bools are never accepted as numbers."""
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(reading=raw))


def test_decimal_rejects_exponent_decimal_object():
    """A Decimal in exponent form would be rewritten on output, so it is rejected."""
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(reading=Decimal("1E+2")))


def test_negative_reading_kept_as_written():
    """Negative readings (e.g. a freezer temperature) are allowed and kept."""
    row = BatchRow.model_validate(batch_data(reading="-20.5"))
    assert row.to_csv_row()["reading"] == "-20.5"


# --- Review fix C: whitespace-only values -----------------------------------


@pytest.mark.parametrize("field", ["product_name", "step_id"])
def test_master_required_field_whitespace_only_rejected(field):
    """A whitespace-only value is not a real value for a required field."""
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(**{field: "   "}))


@pytest.mark.parametrize("field", BATCH_KEY_FIELDS)
def test_batch_key_field_whitespace_only_rejected(field):
    """A whitespace-only join key would silently match nothing, so it is rejected."""
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(**{field: " "}))


def test_master_parameter_whitespace_only_rejected():
    """A whitespace-only parameter must not pass as a measured step."""
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(parameter=" "))


@pytest.mark.parametrize("field", ["unit", "performed_by", "verified_by"])
def test_batch_optional_text_whitespace_only_rejected(field):
    """Whitespace-only must not hide a missing value; blank is written as ''."""
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(**{field: "  "}))


def test_batch_comment_whitespace_kept_as_written():
    """Comments are free text and are kept exactly, whitespace included."""
    assert BatchRow.model_validate(batch_data(comment="  ")).comment == "  "


def test_required_value_not_stripped():
    """Valid values are never stripped or collapsed."""
    row = MasterRow.model_validate(master_data(product_name="Synthetamol  Tablets"))
    assert row.product_name == "Synthetamol  Tablets"


# --- Review fix D: master unit and strict step_seq --------------------------


def test_master_measured_step_without_unit_rejected():
    """A measured step needs a unit to compare batch readings against."""
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(unit=""))


@pytest.mark.parametrize("raw", ["1.0", True, 1.0, "01", " 1"])
def test_master_step_seq_rejects_non_canonical(raw):
    """step_seq must be a plain positive integer; nothing is coerced."""
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(step_seq=raw))


@pytest.mark.parametrize("raw", [3, "3"])
def test_master_step_seq_accepts_int_and_digit_string(raw):
    """An int or its digit string both give the same step_seq."""
    assert MasterRow.model_validate(master_data(step_seq=raw)).step_seq == 3


# --- Review 2 fix: ASCII digits and header check ----------------------------


@pytest.mark.parametrize(
    ("model", "data"),
    [
        pytest.param(BatchRow, batch_data(reading="١٢"), id="reading"),
        pytest.param(MasterRow, master_data(batch_size="٥"), id="batch_size"),
    ],
)
def test_decimal_rejects_non_ascii_digits(model, data):
    """Non-ASCII digits would be rewritten as ASCII on output, so they are rejected."""
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_step_seq_rejects_non_ascii_digits():
    """step_seq accepts ASCII digits only."""
    with pytest.raises(ValidationError):
        MasterRow.model_validate(master_data(step_seq="٣"))


def test_timestamp_rejects_non_ascii_digits():
    """Timestamps accept ASCII digits only."""
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(performed_at="٢٠٢٦-01-15T08:30"))


def test_check_header_accepts_real_models():
    """Both models match their client header (also checked at import time)."""
    check_header(MasterRow, MASTER_HEADER)
    check_header(BatchRow, BATCH_HEADER)


def test_check_header_raises_on_mismatch():
    """A model whose field order differs from its header is caught immediately."""
    with pytest.raises(RuntimeError):
        check_header(MasterRow, tuple(reversed(MASTER_HEADER)))


# --- Review 3 fix: timestamp input types ------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"2026-10-12T07:00:59", id="bytes_with_seconds"),
        pytest.param(b"2026-10-12T07:00", id="bytes"),
        pytest.param(1760252400, id="unix_int"),
        pytest.param(1760252400.0, id="unix_float"),
    ],
)
def test_batch_timestamp_rejects_non_text_non_datetime(raw):
    """Only text or a datetime is accepted; other types would skip the minute check."""
    with pytest.raises(ValidationError):
        BatchRow.model_validate(batch_data(performed_at=raw))


# --- Public contract helpers ------------------------------------------------


def test_csv_row_and_check_header_are_public_contract():
    """Other modules build CSV row types on the public CsvRow + check_header."""

    class TwoColumns(CsvRow):
        """A minimal row type defined outside schema.py."""

        a: Text
        b: Text

    check_header(TwoColumns, ("a", "b"))
    assert TwoColumns(a="x", b="y").to_csv_row() == {"a": "x", "b": "y"}
    with pytest.raises(RuntimeError):
        check_header(TwoColumns, ("b", "a"))


def test_deprecated_private_aliases_still_work():
    """Code written before the public rename (e.g. records.py) keeps working.

    _CsvRow and _check_header are deprecated aliases of the public names; remove
    them only after every caller has moved to CsvRow / check_header.
    """
    assert schema_module._CsvRow is CsvRow
    assert schema_module._check_header is check_header
