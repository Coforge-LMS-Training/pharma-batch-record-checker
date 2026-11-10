"""A catalog of extra synthetic products, as a folder of TOML files (--catalog DIR).

    catalog/
      templates/      one process template per file: its ordered [[step]]s
      defect_plans/   one defect plan per file, for one template (optional folder)
      products/       one product per file: identity, template, limits, plan

Each item is named by its file stem (templates/tablet.toml is template
"tablet"), and items refer to each other by those names. Files load in sorted
name order, so the result is the same on every machine.

Like the CSV contract, input is checked, never normalized: every value is
quoted text written exactly as it will appear in the CSV (a bare TOML number
is refused rather than converted), `critical` is a TOML boolean, and unknown
keys are refused so a typo can't be silently ignored. Every error is a
ValueError that starts with the file it is about. All data must be synthetic.

The two approved products stay as code constants in masters.py and
defects.py; the catalog's tablet and capsule files are copies of them, kept
equal by tests.
"""

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from pharma_batch_record_checker.generator.defects import Defect
from pharma_batch_record_checker.generator.expected_findings import RuleId
from pharma_batch_record_checker.generator.masters import (
    ProductConfig,
    StepTemplate,
    build_master,
)

Text = Annotated[str, StringConstraints(strict=True)]
Letter = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Z]$")]
# [min, max] as written; a TOML array is a list, so it is converted to a tuple.
Limit = Annotated[list[Text], Field(min_length=2, max_length=2)]

# What a file stem must look like to be used as a name.
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class _Strict(BaseModel):
    """Base for catalog models: unknown keys are errors."""

    model_config = ConfigDict(extra="forbid", strict=True)


class _StepEntry(_Strict):
    """One [[step]] of a template, as written.

    A measured step has both parameter and unit; a sign-off-only step has
    neither (StepTemplate enforces this).
    """

    step_id: Text
    unit_procedure: Text
    operation: Text
    phase: Text
    parameter: Text | None = None
    unit: Text | None = None
    critical: bool


class _TemplateFile(_Strict):
    """A template file: a description and at least one step."""

    description: Text
    step: list[_StepEntry] = Field(min_length=1)


def load_templates(
    folder: str | os.PathLike[str],
) -> Mapping[str, tuple[StepTemplate, ...]]:
    """Load every templates/<name>.toml as {name: steps}, in name order.

    Raises ValueError naming the file for invalid TOML, a missing, unknown or
    wrong-typed key, an invalid step, or a step_id used twice.
    """
    templates = {}
    for name, path in _named_files(folder):
        parsed = _parse(path, _TemplateFile)
        steps = tuple(_step(path, entry) for entry in parsed.step)
        ids = [s.step_id for s in steps]
        if duplicates := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"{path}: duplicate step_ids {duplicates}")
        templates[name] = steps
    return MappingProxyType(templates)


def _step(path: Path, entry: _StepEntry) -> StepTemplate:
    """The StepTemplate an entry describes; its own checks name the file."""
    try:
        return StepTemplate(**entry.model_dump())
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from None


# --- Defect plans ---------------------------------------------------------------


@dataclass(frozen=True)
class DefectPlanSpec:
    """A defect plan from the catalog: the defects to plant on one template.

    comments maps a defective step_id to an inert, instruction-like comment.
    """

    template: str
    defects: tuple[Defect, ...]
    comments: Mapping[str, str] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        """Keep a read-only copy of comments, so a plan can't change in place."""
        object.__setattr__(self, "comments", MappingProxyType(dict(self.comments)))


class _DefectEntry(_Strict):
    """One [[defect]]: break rule_id on step_id (side/unit as in Defect)."""

    step_id: Text
    rule_id: RuleId
    side: Literal["low", "high"] | None = None
    unit: Text | None = None


class _PlanFile(_Strict):
    """A defect plan file: its template, at least one defect, and comments."""

    template: Text
    defect: list[_DefectEntry] = Field(min_length=1)
    comments: dict[Text, Text] = Field(default_factory=dict)


def load_defect_plans(
    folder: str | os.PathLike[str],
    templates: Mapping[str, tuple[StepTemplate, ...]],
) -> Mapping[str, DefectPlanSpec]:
    """Load every defect_plans/<name>.toml as {name: plan}, in name order.

    The folder is optional: if it doesn't exist there are no plans. Each plan
    must name a loaded template, put every defect on a step of it, and put
    comments only on steps it breaks. Whether the defects can actually be
    planted depends on a product's limits, so that is checked per product.
    """
    if not Path(folder).exists():
        return MappingProxyType({})
    plans = {}
    for name, path in _named_files(folder):
        parsed = _parse(path, _PlanFile)
        steps = templates.get(parsed.template)
        if steps is None:
            raise ValueError(f"{path}: unknown template {parsed.template!r}")
        step_ids = {s.step_id for s in steps}
        defects = []
        for entry in parsed.defect:
            if entry.step_id not in step_ids:
                raise ValueError(
                    f"{path}: {entry.step_id} is not a step of template "
                    f"{parsed.template!r}"
                )
            try:
                defects.append(Defect(**entry.model_dump()))
            except ValueError as error:
                raise ValueError(f"{path}: {error}") from None
        defective = {d.step_id for d in defects}
        if stray := sorted(set(parsed.comments) - defective):
            raise ValueError(
                f"{path}: comments go only on steps with a defect, not {stray}"
            )
        plans[name] = DefectPlanSpec(parsed.template, tuple(defects), parsed.comments)
    return MappingProxyType(plans)


# --- Products and the whole catalog ------------------------------------------


@dataclass(frozen=True)
class CatalogProduct:
    """One product from the catalog: its config, ID letter and defect plan.

    defect_plan is None for a product that only gets clean batches.
    """

    product: ProductConfig
    prefix: str
    defect_plan: DefectPlanSpec | None


@dataclass(frozen=True)
class Catalog:
    """Everything a catalog folder holds, each part in file-name order."""

    templates: Mapping[str, tuple[StepTemplate, ...]]
    plans: Mapping[str, DefectPlanSpec]
    products: tuple[CatalogProduct, ...]


class _ProductFile(_Strict):
    """A product file, as written: identity, template, ID letter, limits, plan."""

    master_record_id: Text
    master_version: Text
    product_name: Text
    batch_size: Text
    batch_size_unit: Text
    excipient: Text
    template: Text
    id_letter: Letter
    defect_plan: Text | None = None
    limits: dict[Text, Limit]


def load_catalog(folder: str | os.PathLike[str]) -> Catalog:
    """Load a catalog folder: templates/, optional defect_plans/, products/.

    Each product must name a loaded template and, if it has one, a plan for
    that same template; its master is built here, so limit coverage and the
    CSV contract are checked at load. The catalog needs at least one product.
    Raises ValueError naming the file (or folder) at fault.
    """
    root = Path(folder)
    templates = load_templates(root / "templates")
    plans = load_defect_plans(root / "defect_plans", templates)
    products = tuple(
        _product(path, templates, plans) for _, path in _named_files(root / "products")
    )
    if not products:
        raise ValueError(f"{root / 'products'}: no products")
    return Catalog(templates, plans, products)


def _product(
    path: Path,
    templates: Mapping[str, tuple[StepTemplate, ...]],
    plans: Mapping[str, DefectPlanSpec],
) -> CatalogProduct:
    """The CatalogProduct one product file describes, fully checked."""
    entry = _parse(path, _ProductFile)
    process = templates.get(entry.template)
    if process is None:
        raise ValueError(f"{path}: unknown template {entry.template!r}")
    plan = None
    if entry.defect_plan is not None:
        plan = plans.get(entry.defect_plan)
        if plan is None:
            raise ValueError(f"{path}: unknown defect_plan {entry.defect_plan!r}")
        if plan.template != entry.template:
            raise ValueError(
                f"{path}: defect_plan {entry.defect_plan!r} is for template "
                f"{plan.template!r}, not {entry.template!r}"
            )
    config = ProductConfig(
        master_record_id=entry.master_record_id,
        master_version=entry.master_version,
        product_name=entry.product_name,
        batch_size=entry.batch_size,
        batch_size_unit=entry.batch_size_unit,
        excipient=entry.excipient,
        process=process,
        limits={step: (low, high) for step, (low, high) in entry.limits.items()},
    )
    try:
        build_master(config)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from None
    return CatalogProduct(config, entry.id_letter, plan)


# --- Reading files --------------------------------------------------------------


def _named_files(folder: str | os.PathLike[str]) -> list[tuple[str, Path]]:
    """(name, path) for each *.toml in folder, sorted; names must be simple."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"{folder}: no such folder")
    files = []
    for path in sorted(folder.glob("*.toml")):
        if not NAME_PATTERN.match(path.stem):
            raise ValueError(
                f"{path}: file name must be a lower-case name "
                "(letters, digits, underscores), e.g. oral_liquid.toml"
            )
        files.append((path.stem, path))
    return files


def _parse[M: BaseModel](path: Path, model: type[M]) -> M:
    """Read path as TOML and validate it with model, naming path on error."""
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except OSError as error:
        raise ValueError(f"{path}: cannot read: {error.strerror or error}") from None
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path}: invalid TOML: {error}") from None
    try:
        return model.model_validate(raw)
    except ValidationError as error:
        reasons = "; ".join(_describe(e) for e in error.errors())
        raise ValueError(f"{path}: {reasons}") from None


def _describe(error: Any) -> str:
    """One validation error as 'step[2].unit: message' (list positions 1-based)."""
    parts: list[str] = []
    for part in error["loc"]:
        if isinstance(part, int) and parts:
            parts[-1] += f"[{part + 1}]"
        else:
            parts.append(str(part))
    return f"{'.'.join(parts)}: {error['msg']}"
