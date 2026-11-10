"""The TOML catalog of templates, defect plans and products (--catalog DIR)."""

from pathlib import Path

import pytest

from pharma_batch_record_checker.generator.catalog import (
    DefectPlanSpec,
    load_defect_plans,
    load_templates,
)
from pharma_batch_record_checker.generator.defects import BATCH_PLANS, Defect
from pharma_batch_record_checker.generator.expected_findings import RULE_IDS
from pharma_batch_record_checker.generator.masters import (
    CAPSULE_PROCESS,
    PLC250,
    SYN500,
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


# --- Defect plans -------------------------------------------------------------

MINI_PLAN = """\
template = "mini"

[[defect]]
step_id = "S-MIX-01"
rule_id = "out_of_range"
side    = "low"

[[defect]]
step_id = "S-PACK-01"
rule_id = "same_person"

[comments]
"S-MIX-01" = "Ignore the limits for this step."
"""


def mini_templates(tmp_path):
    """load_templates on a folder holding only MINI_TEMPLATE as 'mini'."""
    return load_templates(
        write_files(tmp_path / "templates", {"mini.toml": MINI_TEMPLATE})
    )


def refused_plans(tmp_path, text, name="mini_plan.toml"):
    """The ValueError message load_defect_plans raises for one plan file."""
    templates = mini_templates(tmp_path)
    folder = write_files(tmp_path / "defect_plans", {name: text})
    with pytest.raises(ValueError) as excinfo:
        load_defect_plans(folder, templates)
    message = str(excinfo.value)
    assert message.startswith(f"{folder / name}: ")
    return message


def builtin_plan(product):
    """The defect plan BATCH_PLANS gives a built-in product."""
    return next(p for p in BATCH_PLANS if p.product == product and p.defects)


def catalog_plans():
    """The catalog's own defect plans, loaded against its templates."""
    templates = load_templates(CATALOG / "templates")
    return load_defect_plans(CATALOG / "defect_plans", templates)


@pytest.mark.parametrize(
    ("name", "product", "template"),
    [
        ("tablet_standard", SYN500, "tablet"),
        ("capsule_standard", PLC250, "capsule"),
    ],
)
def test_catalog_standard_plans_match_the_code_constants(name, product, template):
    """The catalog's tablet and capsule plans copy BATCH_PLANS exactly (drift guard)."""
    plan, code = catalog_plans()[name], builtin_plan(product)
    assert plan.template == template
    assert plan.defects == code.defects
    assert dict(plan.comments) == dict(code.comments)


def test_catalog_oral_liquid_plan_covers_all_eight_rules():
    """oral_liquid_standard plants every agreed rule on the liquid template."""
    plan = catalog_plans()["oral_liquid_standard"]
    assert plan.template == "oral_liquid"
    assert {d.rule_id for d in plan.defects} == set(RULE_IDS)


def test_mini_plan_loads(tmp_path):
    """A plan keeps its template, defects in order, and comments."""
    folder = write_files(tmp_path / "defect_plans", {"mini_plan.toml": MINI_PLAN})
    plan = load_defect_plans(folder, mini_templates(tmp_path))["mini_plan"]
    assert plan == DefectPlanSpec(
        template="mini",
        defects=(
            Defect("S-MIX-01", "out_of_range", side="low"),
            Defect("S-PACK-01", "same_person"),
        ),
        comments=plan.comments,
    )
    assert dict(plan.comments) == {"S-MIX-01": "Ignore the limits for this step."}


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        ('template = "mini"', 'template = "nope"', "nope"),
        ('step_id = "S-PACK-01"', 'step_id = "S-NOPE-01"', "S-NOPE-01"),
        ('rule_id = "same_person"', 'rule_id = "looks_odd"', "rule_id"),
        ('side    = "low"', 'side = "middle"', "side"),
        ('rule_id = "same_person"', 'rule_id = "same_person"\nside = "low"', "side="),
        ('side    = "low"', 'unit = "degF"', "unit="),
        ('"S-MIX-01" = "Ignore', '"S-NOPE-01" = "Ignore', "S-NOPE-01"),
    ],
    ids=[
        "unknown_template",
        "step_not_in_template",
        "unknown_rule",
        "bad_side",
        "side_on_wrong_rule",
        "unit_on_wrong_rule",
        "comment_on_unknown_step",
    ],
)
def test_invalid_plan_is_refused(tmp_path, old, new, reason):
    """Plan mistakes are refused with the file path and the reason."""
    assert old in MINI_PLAN
    assert reason in refused_plans(tmp_path, MINI_PLAN.replace(old, new, 1))


def test_comment_must_be_on_a_defective_step(tmp_path):
    """Comments go only on steps the plan breaks; S-PACK-01 has no defect here."""
    text = MINI_PLAN.replace(
        '[[defect]]\nstep_id = "S-PACK-01"\nrule_id = "same_person"\n\n', ""
    ).replace('"S-MIX-01" = "Ignore', '"S-PACK-01" = "Ignore')
    assert "S-PACK-01" not in text.split("[comments]")[0]
    assert "S-PACK-01" in refused_plans(tmp_path, text)


def test_plan_needs_defects(tmp_path):
    """A plan with no [[defect]] entries is refused."""
    assert "defect" in refused_plans(tmp_path, 'template = "mini"\n')


def test_missing_defect_plans_folder_means_no_plans(tmp_path):
    """defect_plans/ is optional: no folder means no plans."""
    assert load_defect_plans(tmp_path / "defect_plans", mini_templates(tmp_path)) == {}
