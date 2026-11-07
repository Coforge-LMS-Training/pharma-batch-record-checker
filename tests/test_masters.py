"""Tests for the template-driven master record generator (PHA-34).

All data is synthetic. The engine tests use a tiny made-up process and product
that are not part of the approved masters.
"""

from decimal import Decimal

import pytest

from pharma_batch_record_checker.masters import (
    ProductConfig,
    StepTemplate,
    build_master,
)

# --- Slice 1: template engine -----------------------------------------------

TINY_PROCESS = (
    StepTemplate(
        step_id="S-DISP-01",
        unit_procedure="Dispensing",
        operation="Weigh raw materials",
        phase="Weigh {excipient}",
        parameter="Excipient net weight",
        unit="kg",
        critical=False,
    ),
    StepTemplate(
        step_id="S-MIX-01",
        unit_procedure="Mixing",
        operation="Dry mix",
        phase="Check dryness",
        parameter="Loss on drying",
        unit="%",
        critical=True,
    ),
    StepTemplate(
        step_id="S-PACK-01",
        unit_procedure="Packaging",
        operation="Line setup",
        phase="Line clearance",
        parameter=None,
        unit=None,
        critical=True,
    ),
)


def tiny_product(**overrides):
    """A made-up product on the tiny process; overrides replace config fields."""
    fields = {
        "master_record_id": "MBR-TST010",
        "master_version": "2",
        "product_name": "Testolin 10 mg tablets",
        "batch_size": "1000",
        "batch_size_unit": "tablets",
        "excipient": "starch",
        "process": TINY_PROCESS,
        "limits": {"S-DISP-01": ("9.90", "10.10"), "S-MIX-01": ("", "3.0")},
    }
    fields.update(overrides)
    return ProductConfig(**fields)


def test_build_master_numbers_steps_in_order():
    """step_seq runs 1..n in the order of the process template."""
    rows = build_master(tiny_product())
    assert [(r.step_seq, r.step_id) for r in rows] == [
        (1, "S-DISP-01"),
        (2, "S-MIX-01"),
        (3, "S-PACK-01"),
    ]


def test_build_master_applies_product_identity():
    """Every row carries the product's id, version, name and batch size."""
    for row in build_master(tiny_product()):
        assert (
            row.master_record_id,
            row.master_version,
            row.product_name,
            row.batch_size,
            row.batch_size_unit,
        ) == ("MBR-TST010", "2", "Testolin 10 mg tablets", Decimal(1000), "tablets")


def test_build_master_fills_excipient_placeholder():
    """{excipient} in template text is filled in from the product."""
    assert build_master(tiny_product())[0].phase == "Weigh starch"


def test_build_master_sign_off_step_has_blank_parameter_unit_limits():
    """A sign-off-only step has no parameter, unit or limits; ISA-88 text is kept."""
    out = build_master(tiny_product())[2].to_csv_row()
    assert [out[f] for f in ("parameter", "unit", "min_value", "max_value")] == [
        "",
        "",
        "",
        "",
    ]
    assert (out["unit_procedure"], out["operation"], out["phase"]) == (
        "Packaging",
        "Line setup",
        "Line clearance",
    )
    assert out["critical"] == "Y"


def test_build_master_one_sided_limits():
    """A blank min or max means unbounded on that side."""
    limits = {"S-DISP-01": ("98.0", ""), "S-MIX-01": ("", "3.0")}
    rows = build_master(tiny_product(limits=limits))
    assert (rows[0].min_value, rows[0].max_value) == (Decimal("98.0"), None)
    assert (rows[1].min_value, rows[1].max_value) == (None, Decimal("3.0"))


def test_build_master_rejects_missing_limits():
    """Every measured step needs a limits entry."""
    with pytest.raises(ValueError, match="S-MIX-01"):
        build_master(tiny_product(limits={"S-DISP-01": ("9.90", "10.10")}))


def test_build_master_rejects_extra_limits():
    """Limits for a step the process doesn't have are a config mistake."""
    limits = {
        "S-DISP-01": ("9.90", "10.10"),
        "S-MIX-01": ("", "3.0"),
        "S-NOPE-99": ("1", "2"),
    }
    with pytest.raises(ValueError, match="S-NOPE-99"):
        build_master(tiny_product(limits=limits))


def test_build_master_rejects_limits_on_sign_off_step():
    """A sign-off-only step can't be given limits."""
    limits = {
        "S-DISP-01": ("9.90", "10.10"),
        "S-MIX-01": ("", "3.0"),
        "S-PACK-01": ("1", "2"),
    }
    with pytest.raises(ValueError, match="S-PACK-01"):
        build_master(tiny_product(limits=limits))
