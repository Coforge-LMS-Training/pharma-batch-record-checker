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
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from pharma_batch_record_checker.generator.masters import StepTemplate

Text = Annotated[str, StringConstraints(strict=True)]

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
