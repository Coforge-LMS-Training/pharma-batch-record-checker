"""The TOML catalog of templates, defect plans and products (--catalog DIR)."""

from pathlib import Path

import pytest

from pharma_batch_record_checker.generator.catalog import load_templates
from pharma_batch_record_checker.generator.masters import (
    CAPSULE_PROCESS,
    TABLET_PROCESS,
    StepTemplate,
)

CATALOG = Path(__file__).resolve().parents[2] / "catalog"

# A two-step template: one measured critical step, one sign-off step.
MINI_TEMPLATE = """\
description = "Two-step test process"

[[step]]
step_id        = "S-MIX-01"
unit_procedure = "Compounding"
operation      = "Dissolve"
phase          = "Mix"
parameter      = "Mixing time"
unit           = "min"
critical       = true

[[step]]
step_id        = "S-PACK-01"
unit_procedure = "Packaging"
operation      = "Line setup"
phase          = "Line clearance"
critical       = true
"""


def write_files(folder, files):
    """Write {file name: text} into folder (created) and return folder."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def refused_templates(tmp_path, text, name="mini.toml"):
    """The ValueError message load_templates raises for one template file."""
    folder = write_files(tmp_path / "templates", {name: text})
    with pytest.raises(ValueError) as excinfo:
        load_templates(folder)
    message = str(excinfo.value)
    assert message.startswith(f"{folder / name}: ")
    return message


# --- Templates ----------------------------------------------------------------


def test_catalog_tablet_template_matches_the_code_constant():
    """templates/tablet.toml is an exact copy of TABLET_PROCESS (drift guard)."""
    assert load_templates(CATALOG / "templates")["tablet"] == TABLET_PROCESS


def test_catalog_capsule_template_matches_the_code_constant():
    """templates/capsule.toml is an exact copy of CAPSULE_PROCESS (drift guard)."""
    assert load_templates(CATALOG / "templates")["capsule"] == CAPSULE_PROCESS


def test_catalog_has_an_oral_liquid_template():
    """oral_liquid is a non-solid form: 10 steps, S-PACK-01 the only sign-off."""
    steps = load_templates(CATALOG / "templates")["oral_liquid"]
    assert len(steps) == 10
    assert [s.step_id for s in steps if s.parameter is None] == ["S-PACK-01"]
    assert {s.unit for s in steps} >= {"pH", "mL"}


def test_templates_are_named_by_file_stem_in_sorted_order(tmp_path):
    """A template's name is its file stem; templates load in file-name order."""
    folder = write_files(
        tmp_path, {"zeta.toml": MINI_TEMPLATE, "alpha.toml": MINI_TEMPLATE}
    )
    templates = load_templates(folder)
    assert list(templates) == ["alpha", "zeta"]
    assert templates["alpha"] == (
        StepTemplate(
            "S-MIX-01", "Compounding", "Dissolve", "Mix", "Mixing time", "min", True
        ),
        StepTemplate(
            "S-PACK-01", "Packaging", "Line setup", "Line clearance", None, None, True
        ),
    )


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        ('step_id        = "S-PACK-01"', 'step_id = "S-MIX-01"', "S-MIX-01"),
        ('unit           = "min"\n', "", "parameter and unit"),
        ('unit           = "min"', "unit = 5", "unit"),
        ("critical       = true\n\n", 'critical = "yes"\n\n', "critical"),
        ('phase          = "Mix"', 'phase = "Mix"\nphse = "x"', "phse"),
        ('unit           = "min"', 'unit = ""', "unit"),
    ],
    ids=[
        "duplicate_step_id",
        "parameter_without_unit",
        "numeric_unit",
        "string_critical",
        "unknown_key",
        "empty_unit",
    ],
)
def test_invalid_template_is_refused(tmp_path, old, new, reason):
    """Template mistakes are refused with the file path and the reason."""
    assert old in MINI_TEMPLATE
    assert reason in refused_templates(tmp_path, MINI_TEMPLATE.replace(old, new, 1))


def test_template_needs_steps(tmp_path):
    """A template without [[step]] entries is refused."""
    assert "step" in refused_templates(tmp_path, 'description = "nothing"\n')


def test_template_name_must_be_a_simple_lower_case_name(tmp_path):
    """File stems become names, so they must look like names."""
    assert "name" in refused_templates(tmp_path, MINI_TEMPLATE, name="Oral-Liquid.toml")


def test_invalid_toml_template_is_refused(tmp_path):
    """A TOML syntax error names the file."""
    assert "invalid TOML" in refused_templates(tmp_path, "[[step]\n")


def test_missing_templates_folder_is_refused(tmp_path):
    """The templates folder must exist."""
    with pytest.raises(ValueError, match="no such folder"):
        load_templates(tmp_path / "templates")
