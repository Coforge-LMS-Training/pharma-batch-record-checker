"""Shared CSV contract for master and executed batch records.

The column names and their order are client-approved and mandatory. The model
fields are declared in exactly the same order as the headers.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

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
    """One step of a master record (master_record.csv)."""

    master_record_id: str
    master_version: str
    product_name: str
    batch_size: Decimal
    batch_size_unit: str
    step_seq: int
    step_id: str
    unit_procedure: str
    operation: str
    phase: str
    parameter: str | None
    unit: str | None
    min_value: Decimal | None
    max_value: Decimal | None
    critical: bool


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
