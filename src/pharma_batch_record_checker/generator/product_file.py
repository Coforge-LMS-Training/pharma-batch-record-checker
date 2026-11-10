"""Extra synthetic products read from a TOML file (generate-batch-records --products).

Each `[[product]]` names a built-in process template ("tablet" or "capsule")
and gives the product's identity, batch size, excipient, ID letter and limits.
An optional `defects_like` names a built-in master whose defect plan the
product reuses; the CLI checks and applies it. See examples/products.toml.

Like the CSV contract, input is checked, never normalized: every value is
text written exactly as it will appear in the CSV, so a bare TOML number is
refused rather than converted (a float would lose how it was written), and
unknown keys are refused so a typo can't be silently ignored. Each product's
master is built here, so limit and row errors surface when the file loads.
All data must be synthetic.
"""

import os
import tomllib
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from pharma_batch_record_checker.generator.masters import (
    CAPSULE_PROCESS,
    TABLET_PROCESS,
    ProductConfig,
    StepTemplate,
    build_master,
)

# Built-in process templates a file product can name.
TEMPLATES: dict[str, tuple[StepTemplate, ...]] = {
    "tablet": TABLET_PROCESS,
    "capsule": CAPSULE_PROCESS,
}

Text = Annotated[str, StringConstraints(strict=True)]
Letter = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Z]$")]


@dataclass(frozen=True)
class FileProduct:
    """One product from the file: its config, ID letter and defect source.

    defects_like is the master_record_id of a built-in product whose defect
    plan to reuse, or None for clean batches only.
    """

    product: ProductConfig
    prefix: str
    defects_like: str | None


class _ProductEntry(BaseModel):
    """One [[product]] table, exactly as written."""

    model_config = ConfigDict(extra="forbid")

    master_record_id: Text
    master_version: Text
    product_name: Text
    batch_size: Text
    batch_size_unit: Text
    excipient: Text
    process: Literal["tablet", "capsule"]
    id_letter: Letter
    defects_like: Text | None = None
    limits: dict[Text, tuple[Text, Text]]


class _ProductFile(BaseModel):
    """The whole file: one or more [[product]] tables and nothing else."""

    model_config = ConfigDict(extra="forbid")

    product: list[_ProductEntry] = Field(min_length=1)


def load_products(path: str | os.PathLike[str]) -> tuple[FileProduct, ...]:
    """Read, validate and build the products in a TOML file, in file order.

    Raises ValueError starting with the path for an unreadable file, invalid
    TOML, a missing, unknown or wrong-typed field, or a master that can't be
    built from the product (limits or CSV-contract errors).
    """
    name = os.fspath(path)
    try:
        with open(name, "rb") as f:
            raw = tomllib.load(f)
    except OSError as error:
        raise ValueError(f"{name}: cannot read: {error.strerror or error}") from None
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{name}: invalid TOML: {error}") from None
    try:
        parsed = _ProductFile.model_validate(raw)
    except ValidationError as error:
        reasons = "; ".join(_describe(e) for e in error.errors())
        raise ValueError(f"{name}: {reasons}") from None
    products = []
    for entry in parsed.product:
        config = _config(entry)
        try:
            build_master(config)
        except ValueError as error:
            raise ValueError(f"{name}: {error}") from None
        products.append(FileProduct(config, entry.id_letter, entry.defects_like))
    return tuple(products)


def _config(entry: _ProductEntry) -> ProductConfig:
    """The ProductConfig an entry describes, with its named process template."""
    return ProductConfig(
        master_record_id=entry.master_record_id,
        master_version=entry.master_version,
        product_name=entry.product_name,
        batch_size=entry.batch_size,
        batch_size_unit=entry.batch_size_unit,
        excipient=entry.excipient,
        process=TEMPLATES[entry.process],
        limits=entry.limits,
    )


def _describe(error: dict) -> str:
    """One validation error as 'product[1].field: message' (1-based product)."""
    parts: list[str] = []
    for i, part in enumerate(error["loc"]):
        if isinstance(part, int):
            parts[-1] += f"[{part + 1 if i == 1 else part}]"
        else:
            parts.append(str(part))
    return f"{'.'.join(parts)}: {error['msg']}"
