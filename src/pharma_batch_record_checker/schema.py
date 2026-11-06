"""Shared CSV contract for master and executed batch records.

The column names and their order are client-approved and mandatory. The model
fields are declared in exactly the same order as the headers.
"""

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NaiveDatetime,
    StringConstraints,
    field_validator,
    model_validator,
)

# Required text: must be non-empty, but is otherwise kept exactly as written
# (no stripping or case changes; units are compared as exact strings).
RequiredStr = Annotated[str, StringConstraints(min_length=1)]


def _blank_to_none(value: Any) -> Any:
    """Treat an empty CSV cell as "no value". A blank is never 0."""
    return None if value == "" else value


# Timestamps are written as ISO 8601 with no timezone and whole seconds.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _canonical_timestamp(value: Any) -> Any:
    """Allow only blank, an exact YYYY-MM-DDTHH:MM:SS string, or a whole-second
    naive datetime, so a timestamp is written back exactly as it was given."""
    value = _blank_to_none(value)
    if isinstance(value, str) and not _TIMESTAMP_RE.fullmatch(value):
        raise ValueError("timestamp must be YYYY-MM-DDTHH:MM:SS")
    if isinstance(value, datetime) and value.microsecond:
        raise ValueError("timestamp must be whole seconds (no microseconds)")
    return value


def _format_cell(value: Any) -> str:
    """Turn one field value into its CSV cell text."""
    if value is None:
        return ""
    if isinstance(value, bool):  # check before int: bool is a subclass of int
        return "Y" if value else "N"
    if isinstance(value, Decimal):
        return format(value, "f")  # plain notation, trailing zeros kept ("7.50")
    if isinstance(value, datetime):
        return value.strftime(TIMESTAMP_FORMAT)
    return str(value)


class _CsvRow(BaseModel):
    """Base for row models: frozen, no extra columns, and CSV serialization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    def to_csv_row(self) -> dict[str, str]:
        """Return this row as {column: cell text}, in header order."""
        return {
            name: _format_cell(getattr(self, name)) for name in type(self).model_fields
        }


MASTER_HEADER: tuple[str, ...] = (
    "master_record_id",
    "master_version",
    "product_name",
    "batch_size",
    "batch_size_unit",
    "step_seq",
    "step_id",
    "unit_procedure",
    "operation",
    "phase",
    "parameter",
    "unit",
    "min_value",
    "max_value",
    "critical",
)

BATCH_HEADER: tuple[str, ...] = (
    "batch_id",
    "master_record_id",
    "master_version",
    "step_id",
    "reading",
    "unit",
    "performed_by",
    "performed_at",
    "verified_by",
    "verified_at",
    "comment",
)


class MasterRow(_CsvRow):
    """One step of a master record (master_record.csv).

    Limits are inclusive, and a blank limit means no limit on that side.
    A sign-off-only step has a blank parameter, unit, min_value and max_value.
    """

    master_record_id: RequiredStr
    master_version: RequiredStr
    product_name: RequiredStr
    batch_size: Annotated[Decimal, Field(gt=0)]
    batch_size_unit: RequiredStr
    step_seq: Annotated[int, Field(ge=1)]
    step_id: RequiredStr
    unit_procedure: RequiredStr
    operation: RequiredStr
    phase: RequiredStr
    parameter: str | None
    unit: str | None
    min_value: Decimal | None
    max_value: Decimal | None
    critical: bool

    _blanks = field_validator(
        "parameter", "unit", "min_value", "max_value", mode="before"
    )(_blank_to_none)

    @field_validator("critical", mode="before")
    @classmethod
    def _critical_y_n(cls, value: Any) -> Any:
        """Accept only Y/N (or a bool); Pydantic alone would also take yes/1/on."""
        if isinstance(value, bool):
            return value
        if value == "Y":
            return True
        if value == "N":
            return False
        raise ValueError("critical must be 'Y' or 'N'")

    @model_validator(mode="after")
    def _check_limits(self) -> "MasterRow":
        """Sign-off-only steps have no unit or limits; when both limits exist, min <= max."""
        if self.parameter is None and (
            self.unit is not None
            or self.min_value is not None
            or self.max_value is not None
        ):
            raise ValueError(
                "sign-off-only step (blank parameter) must also have a blank "
                "unit, min_value and max_value"
            )
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.min_value > self.max_value
        ):
            raise ValueError("min_value must not be greater than max_value")
        return self


class BatchRow(_CsvRow):
    """One executed step of a batch (batch_record.csv).

    Only the join key is required. Everything else may be blank, and there are
    no cross-field checks: the generator must be able to plant defects
    (missing reading or performer, verifier before performer, unit mismatch,
    out-of-range reading). Detecting them is the rule engine's job.
    Timestamps have no timezone; timezone-aware values are rejected.
    """

    batch_id: RequiredStr
    master_record_id: RequiredStr
    master_version: RequiredStr
    step_id: RequiredStr
    reading: Decimal | None
    unit: str | None
    performed_by: str | None
    performed_at: NaiveDatetime | None
    verified_by: str | None
    verified_at: NaiveDatetime | None
    comment: str | None

    _blanks = field_validator(
        "reading",
        "unit",
        "performed_by",
        "verified_by",
        "comment",
        mode="before",
    )(_blank_to_none)
    _timestamps = field_validator("performed_at", "verified_at", mode="before")(
        _canonical_timestamp
    )
