"""Tests for the template-driven master record generator (PHA-34).

All data is synthetic. The engine tests use a tiny made-up process and product
that are not part of the approved masters.
"""

import csv
import dataclasses
from decimal import Decimal

import pytest

from pharma_batch_record_checker.masters import (
    PLC250,
    SYN500,
    ProductConfig,
    StepTemplate,
    build_master,
    build_masters,
    write_masters,
)
from pharma_batch_record_checker.schema import MASTER_HEADER

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


# --- Slice 2: tablet process and MBR-SYN500 v3 ------------------------------


def unit_procedures_in_order(rows):
    """Distinct unit procedures in the order they first appear."""
    return list(dict.fromkeys(r.unit_procedure for r in rows))


def row_by_step(rows, step_id):
    """The single row with the given step_id."""
    (row,) = [r for r in rows if r.step_id == step_id]
    return row


def test_syn500_has_16_steps():
    """The approved tablet master has exactly 16 steps, numbered 1..16."""
    assert [r.step_seq for r in build_master(SYN500)] == list(range(1, 17))


def test_syn500_identity():
    """Every SYN500 row carries the approved id, version, product and batch size."""
    for row in build_master(SYN500):
        assert row.to_csv_row()["master_record_id"] == "MBR-SYN500"
        assert (
            row.master_version,
            row.product_name,
            row.to_csv_row()["batch_size"],
            row.batch_size_unit,
        ) == ("3", "Synthetol 500 mg film-coated tablets", "300000", "tablets")


def test_syn500_unit_procedures_in_process_order():
    """Tablet route: dispense, granulate, blend, compress, coat, pack."""
    assert unit_procedures_in_order(build_master(SYN500)) == [
        "Dispensing",
        "Granulation",
        "Blending",
        "Compression",
        "Coating",
        "Packaging",
    ]


def test_syn500_step_ids_unique():
    """step_id identifies a step within the master (it is part of the join key)."""
    ids = [r.step_id for r in build_master(SYN500)]
    assert len(ids) == len(set(ids))


def test_syn500_sign_off_step():
    """Line clearance is a critical sign-off-only step."""
    out = row_by_step(build_master(SYN500), "S-PACK-01").to_csv_row()
    assert (out["parameter"], out["unit"], out["min_value"], out["max_value"]) == (
        "",
        "",
        "",
        "",
    )
    assert out["critical"] == "Y"


@pytest.mark.parametrize(
    ("step_id", "expected"),
    [
        ("S-DISP-01", ("API net weight", "kg", "149.25", "150.75", "Y")),
        ("S-GRAN-05", ("Loss on drying", "%", "1.0", "2.5", "Y")),
        ("S-DISP-02", ("Excipient net weight", "kg", "29.85", "30.15", "N")),
    ],
)
def test_syn500_spot_rows(step_id, expected):
    """Selected steps have the approved parameter, unit, limits and critical flag."""
    out = row_by_step(build_master(SYN500), step_id).to_csv_row()
    assert (
        out["parameter"],
        out["unit"],
        out["min_value"],
        out["max_value"],
        out["critical"],
    ) == expected


def test_syn500_excipient_phase():
    """The excipient dispensing phase names the tablet's excipient."""
    row = row_by_step(build_master(SYN500), "S-DISP-02")
    assert row.phase == "Weigh microcrystalline cellulose"


# --- Slice 3: capsule process and MBR-PLC250 v1 -----------------------------


def test_plc250_has_10_steps():
    """The approved capsule master has exactly 10 steps, numbered 1..10."""
    assert [r.step_seq for r in build_master(PLC250)] == list(range(1, 11))


def test_plc250_identity():
    """Every PLC250 row carries the approved id, version, product and batch size."""
    for row in build_master(PLC250):
        out = row.to_csv_row()
        assert (
            out["master_record_id"],
            out["master_version"],
            out["product_name"],
            out["batch_size"],
            out["batch_size_unit"],
        ) == ("MBR-PLC250", "1", "Placeprol 250 mg hard capsules", "200000", "capsules")


def test_plc250_unit_procedures_in_process_order():
    """Capsule route: dispense, blend, encapsulate, pack."""
    assert unit_procedures_in_order(build_master(PLC250)) == [
        "Dispensing",
        "Blending",
        "Encapsulation",
        "Packaging",
    ]


def test_plc250_step_ids_unique():
    """step_id identifies a step within the master (it is part of the join key)."""
    ids = [r.step_id for r in build_master(PLC250)]
    assert len(ids) == len(set(ids))


def test_plc250_one_sided_limits():
    """Loss on drying is max-only; yield after inspection is min-only."""
    rows = build_master(PLC250)
    lod = row_by_step(rows, "S-BLEND-02").to_csv_row()
    yield_ = row_by_step(rows, "S-ENC-03").to_csv_row()
    assert (lod["min_value"], lod["max_value"]) == ("", "3.0")
    assert (yield_["min_value"], yield_["max_value"]) == ("98.0", "")


def test_plc250_equal_limits():
    """Capsules per bottle must be exactly 30 (inclusive limits, min == max)."""
    out = row_by_step(build_master(PLC250), "S-PACK-02").to_csv_row()
    assert (out["unit"], out["min_value"], out["max_value"]) == ("count", "30", "30")


def test_plc250_sign_off_step():
    """Line clearance is a critical sign-off-only step in the capsule master too."""
    out = row_by_step(build_master(PLC250), "S-PACK-01").to_csv_row()
    assert (out["parameter"], out["unit"], out["critical"]) == ("", "", "Y")


def test_plc250_excipient_is_lactose():
    """The capsule's excipient dispensing phase names lactose."""
    assert row_by_step(build_master(PLC250), "S-DISP-02").phase == "Weigh lactose"


# --- Slice 4: combined build and write --------------------------------------


def test_build_masters_order_and_count():
    """26 rows: the 16 tablet steps first, then the 10 capsule steps."""
    ids = [r.master_record_id for r in build_masters()]
    assert ids == ["MBR-SYN500"] * 16 + ["MBR-PLC250"] * 10


def test_build_masters_is_deterministic():
    """Building twice gives identical masters (no randomness)."""
    assert build_masters() == build_masters()


def test_masters_cover_required_features():
    """The output includes sign-off steps, min-only and max-only limits, and Y and N."""
    rows = build_masters()
    assert any(r.parameter is None for r in rows)
    assert any(r.min_value is not None and r.max_value is None for r in rows)
    assert any(r.min_value is None and r.max_value is not None for r in rows)
    assert {r.critical for r in rows} == {True, False}


def test_write_masters_round_trip(tmp_path):
    """The written file has the exact header, 26 rows and CRLF line endings."""
    path = tmp_path / "master_record.csv"
    write_masters(path)
    lines = path.read_bytes().decode("utf-8").split("\r\n")
    assert lines[0] == ",".join(MASTER_HEADER)
    assert lines[-1] == ""  # file ends with CRLF
    assert len(lines) - 2 == 26
    with path.open(newline="", encoding="utf-8") as f:
        assert list(csv.DictReader(f)) == [r.to_csv_row() for r in build_masters()]


def test_new_product_reuses_tablet_process():
    """A new product can be added as config alone; the generator isn't tied to SYN500."""
    other = dataclasses.replace(
        SYN500,
        master_record_id="MBR-TST250",
        master_version="1",
        product_name="Testolin 250 mg film-coated tablets",
        batch_size="150000",
        excipient="mannitol",
    )
    rows = build_master(other)
    assert len(rows) == 16
    assert {r.master_record_id for r in rows} == {"MBR-TST250"}
    assert row_by_step(rows, "S-DISP-02").phase == "Weigh mannitol"
