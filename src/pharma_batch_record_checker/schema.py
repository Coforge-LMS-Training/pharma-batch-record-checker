"""Shared CSV contract for master and executed batch records.

The column names and their order are client-approved and mandatory. The model
fields are declared in exactly the same order as the headers.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
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


class MasterRow(BaseModel):
    """One step of a master record (master_record.csv).

    Limits are inclusive, and a blank limit means no limit on that side.
    A sign-off-only step has a blank parameter, unit, min_value and max_value.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

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
        # Pydantic's own bool parsing would also accept "yes", "1", "on", ...
        # The CSV contract allows only Y or N.
        if isinstance(value, bool):
            return value
        if value == "Y":
            return True
        if value == "N":
            return False
        raise ValueError("critical must be 'Y' or 'N'")

    @model_validator(mode="after")
    def _check_limits(self) -> "MasterRow":
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


class BatchRow(BaseModel):
    """One executed step of a batch (batch_record.csv)."""

    batch_id: str
    master_record_id: str
    master_version: str
    step_id: str
    reading: Decimal | None
    unit: str | None
    performed_by: str | None
    performed_at: datetime | None
    verified_by: str | None
    verified_at: datetime | None
    comment: str | None
