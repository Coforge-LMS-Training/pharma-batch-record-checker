"""The seeded generator command, generate-batch-records (PHA-38)."""

import csv
import dataclasses
import shutil
import subprocess
import sys
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import pytest

import pharma_batch_record_checker.generator.cli as cli_module
from pharma_batch_record_checker.generator.catalog import CatalogProduct, load_catalog
from pharma_batch_record_checker.generator.cli import (
    CHANGEOVER,
    MAX_BATCHES,
    PRODUCT_LINES,
    ProductLine,
    batch_span,
    extra_lines,
    main,
    parse_start,
    plan_batches,
)
from pharma_batch_record_checker.generator.dataset_io import (
    BATCH_FILE,
    EXPECTED_FILE,
    MASTER_FILE,
)
from pharma_batch_record_checker.generator.defects import build_dataset
from pharma_batch_record_checker.generator.expected_findings import (
    EXPECTED_FINDINGS_HEADER,
    RULE_IDS,
)
from pharma_batch_record_checker.generator.masters import SYN500
from pharma_batch_record_checker.schema import (
    BATCH_HEADER,
    MASTER_HEADER,
    BatchRow,
    MasterRow,
)
from tests.generator.rule_oracle import rule_violations

# --- Planning N batches -------------------------------------------------------


def ids(plans):
    """Batch IDs of the plans, in order."""
    return [p.batch_id for p in plans]


def test_plans_alternate_products():
    """Batches alternate between the tablet and the capsule master."""
    products = [p.product.master_record_id for p in plan_batches(6)]
    assert products == ["MBR-SYN500", "MBR-PLC250"] * 3


@pytest.mark.parametrize("n", [1, 2, 5, 12])
def test_batch_count_matches_n(n):
    """--batches N gives exactly N batches."""
    assert len(plan_batches(n)) == n


def test_batch_ids_number_per_product():
    """Each product counts its own batches: B26-0001, P26-0001, B26-0002, ..."""
    assert ids(plan_batches(5)) == [
        "B26-0001",
        "P26-0001",
        "B26-0002",
        "P26-0002",
        "B26-0003",
    ]


def test_defects_on_every_other_batch_per_product():
    """Within a product, batches alternate clean and defective, starting clean."""
    defective = [bool(p.defects) for p in plan_batches(8)]
    assert defective == [False, False, True, True, False, False, True, True]


def test_four_batches_cover_all_eight_rules():
    """Already at N=4 the dataset plants every agreed rule."""
    dataset = build_dataset(0, plan_batches(4))
    assert {f.rule_id for f in dataset.findings} == set(RULE_IDS)


def test_start_times_follow_back_to_back_schedule():
    """Each line starts its next batch one span plus a changeover after the last.

    Tablet cycle is 10 h 50 min, capsule 7 h 50 min, both from 2026-10-12 07:00.
    """
    starts = {p.batch_id: p.start for p in plan_batches(6)}
    assert starts == {
        "B26-0001": datetime(2026, 10, 12, 7, 0),
        "P26-0001": datetime(2026, 10, 12, 7, 0),
        "B26-0002": datetime(2026, 10, 12, 17, 50),
        "P26-0002": datetime(2026, 10, 12, 14, 50),
        "B26-0003": datetime(2026, 10, 13, 4, 40),
        "P26-0003": datetime(2026, 10, 12, 22, 40),
    }


def by_line(plans):
    """Plans grouped by master_record_id, in plan order."""
    grouped = {}
    for plan in plans:
        grouped.setdefault(plan.product.master_record_id, []).append(plan)
    return grouped


def rows_by_batch(dataset):
    """The dataset's batch rows grouped by batch_id."""
    grouped = {}
    for row in dataset.batches:
        grouped.setdefault(row.batch_id, []).append(row)
    return grouped


def test_each_line_waits_for_previous_batch():
    """On a line, starts are batch_span + CHANGEOVER apart and never overlap.

    The gap is fixed by the plan, and the generated rows of each batch finish
    (performed and verified) before the next batch on its line starts.
    """
    plans = plan_batches(20)
    rows = rows_by_batch(build_dataset(0, plans))
    for line in by_line(plans).values():
        for prev, nxt in pairwise(line):
            assert nxt.start - prev.start == batch_span(prev.product) + CHANGEOVER
            times = [r.performed_at for r in rows[prev.batch_id]] + [
                r.verified_at for r in rows[prev.batch_id]
            ]
            assert max(t for t in times if t is not None) < nxt.start


def test_clean_batch_crossing_midnight_is_unflagged():
    """P26-0003 runs from 22:40 past midnight and still breaks none of the 8 rules."""
    dataset = build_dataset(0, plan_batches(8))
    rows = rows_by_batch(dataset)["P26-0003"]
    assert len({r.performed_at.date() for r in rows if r.performed_at}) == 2
    master = [m for m in dataset.masters if m.master_record_id == "MBR-PLC250"]
    assert rule_violations(master, rows) == []


@pytest.mark.parametrize("n", [0, -1, MAX_BATCHES + 1])
def test_rejects_n_outside_range(n):
    """N must be 1 to MAX_BATCHES, a plain sanity limit."""
    with pytest.raises(ValueError, match="batches"):
        plan_batches(n)


# --- The command --------------------------------------------------------------

FILES = (MASTER_FILE, BATCH_FILE, EXPECTED_FILE)


def run(out, *args):
    """Run the command in-process with --out out plus args; return the exit code."""
    return main(["--out", str(out), *args])


def files_of(out):
    """{file name: bytes} of the three dataset files in out."""
    return {name: (out / name).read_bytes() for name in FILES}


def batch_ids_in(out):
    """Distinct batch IDs in out/batch_record.csv, in order."""
    with (out / BATCH_FILE).open(newline="", encoding="utf-8") as f:
        return list(dict.fromkeys(row["batch_id"] for row in csv.DictReader(f)))


def test_writes_three_files_with_exact_headers(tmp_path):
    """The command writes the three agreed files with their exact headers."""
    assert run(tmp_path, "--seed", "0", "--batches", "4") == 0
    headers = {
        MASTER_FILE: ",".join(MASTER_HEADER),
        BATCH_FILE: ",".join(BATCH_HEADER),
        EXPECTED_FILE: ",".join(EXPECTED_FINDINGS_HEADER),
    }
    for name, header in headers.items():
        assert (tmp_path / name).read_bytes().split(b"\r\n")[0].decode() == header


def test_same_args_byte_identical_across_runs(tmp_path):
    """Same --seed and --batches give byte-identical files."""
    run(tmp_path / "a", "--seed", "3", "--batches", "6")
    run(tmp_path / "b", "--seed", "3", "--batches", "6")
    assert files_of(tmp_path / "a") == files_of(tmp_path / "b")


def test_different_seed_changes_readings(tmp_path):
    """A different --seed changes the batch readings."""
    run(tmp_path / "a", "--seed", "3", "--batches", "6")
    run(tmp_path / "b", "--seed", "4", "--batches", "6")
    assert files_of(tmp_path / "a")[BATCH_FILE] != files_of(tmp_path / "b")[BATCH_FILE]


def test_batches_option_changes_batch_count(tmp_path):
    """--batches controls how many batches are generated."""
    run(tmp_path / "a", "--batches", "3")
    run(tmp_path / "b", "--batches", "7")
    assert len(batch_ids_in(tmp_path / "a")) == 3
    assert len(batch_ids_in(tmp_path / "b")) == 7


def test_defaults_are_seed_0_and_four_batches(tmp_path):
    """Without --seed and --batches: seed 0, four batches."""
    run(tmp_path / "a")
    run(tmp_path / "b", "--seed", "0", "--batches", "4")
    assert files_of(tmp_path / "a") == files_of(tmp_path / "b")


def test_creates_out_dir_and_overwrites_files(tmp_path):
    """A missing --out folder is created; existing dataset files are replaced."""
    out = tmp_path / "new" / "data"
    run(out, "--batches", "6")
    run(out, "--batches", "2")
    assert len(batch_ids_in(out)) == 2


def test_leaves_other_files_alone(tmp_path):
    """Files in --out other than the three dataset files are untouched."""
    (tmp_path / "notes.txt").write_text("keep me")
    run(tmp_path)
    assert (tmp_path / "notes.txt").read_text() == "keep me"


def test_summary_line(tmp_path, capsys):
    """The command prints one deterministic summary line."""
    run(tmp_path, "--seed", "0", "--batches", "4")
    expected = len(build_dataset(0, plan_batches(4)).findings)
    assert capsys.readouterr().out == (
        f"wrote 4 batches, {expected} expected findings to {tmp_path}\n"
    )


def test_out_is_required(capsys):
    """--out has no default, so nothing is written somewhere unexpected."""
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2
    assert "--out" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["0", "-3", "10000", "x"])
def test_rejects_bad_batches(bad, tmp_path, capsys):
    """--batches must be a whole number from 1 to MAX_BATCHES; else a usage error."""
    with pytest.raises(SystemExit) as excinfo:
        run(tmp_path, "--batches", bad)
    assert excinfo.value.code == 2
    assert "--batches" in capsys.readouterr().err


def first_performed_at(out):
    """{batch_id: performed_at of its first row} from out/batch_record.csv."""
    firsts = {}
    with (out / BATCH_FILE).open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            firsts.setdefault(row["batch_id"], row["performed_at"])
    return firsts


def test_start_option_moves_dates(tmp_path):
    """--start changes the dates; the same flags still give byte-identical files."""
    run(tmp_path / "a", "--batches", "8")
    run(tmp_path / "b", "--batches", "8", "--start", "2026-12-31T07:00")
    run(tmp_path / "c", "--batches", "8", "--start", "2026-12-31T07:00")
    assert files_of(tmp_path / "a")[BATCH_FILE] != files_of(tmp_path / "b")[BATCH_FILE]
    assert files_of(tmp_path / "b") == files_of(tmp_path / "c")
    assert "B27-0001" in batch_ids_in(tmp_path / "b")
    assert first_performed_at(tmp_path / "b")["B26-0001"] == "2026-12-31T07:00"


def test_default_start_is_2026_10_12_0700(tmp_path):
    """Without --start, the files equal those of --start 2026-10-12T07:00."""
    run(tmp_path / "a", "--batches", "6")
    run(tmp_path / "b", "--batches", "6", "--start", "2026-10-12T07:00")
    assert files_of(tmp_path / "a") == files_of(tmp_path / "b")


def test_seed_does_not_change_ids_or_start_times(tmp_path):
    """--seed changes readings only: batch IDs and start times stay the same."""
    run(tmp_path / "a", "--seed", "3", "--batches", "8")
    run(tmp_path / "b", "--seed", "4", "--batches", "8")
    assert first_performed_at(tmp_path / "a") == first_performed_at(tmp_path / "b")


@pytest.mark.parametrize(
    "bad",
    [
        "2026-10-12 07:00",
        "2026-10-12T07:00:30",
        "2026-10-12T07:00Z",
        "2026-1-5T7:00",
        "1999-12-31T07:00",
        "2100-01-01T07:00",
        "x",
    ],
)
def test_rejects_bad_start(bad, tmp_path, capsys):
    """--start must be canonical YYYY-MM-DDTHH:MM, year 2000-2099; else a usage error."""
    with pytest.raises(ValueError, match="start"):
        parse_start(bad)
    with pytest.raises(SystemExit) as excinfo:
        run(tmp_path, "--start", bad)
    assert excinfo.value.code == 2
    assert "argument --start" in capsys.readouterr().err
    assert not (tmp_path / BATCH_FILE).exists()


def test_parse_start_accepts_canonical_timestamp():
    """A canonical timestamp parses to the same naive datetime."""
    assert parse_start("2026-12-31T07:00") == datetime(2026, 12, 31, 7, 0)
    assert parse_start("2099-12-31T23:59") == datetime(2099, 12, 31, 23, 59)


def test_plan_batches_rejects_start_outside_2000s():
    """plan_batches also refuses a start year that a two-digit YY can't name."""
    with pytest.raises(ValueError, match="start"):
        plan_batches(1, datetime(1999, 12, 31, 7, 0))


def test_runs_as_module_byte_identical_twice(tmp_path):
    """As a separate process, the same arguments give byte-identical files."""
    for name in ("a", "b"):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pharma_batch_record_checker.generator.cli",
                "--seed",
                "5",
                "--batches",
                "4",
                "--out",
                str(tmp_path / name),
            ],
            check=True,
            capture_output=True,
        )
    assert files_of(tmp_path / "a") == files_of(tmp_path / "b")


# --- Review follow-ups ---------------------------------------------------------


def test_batch_ids_use_product_line_prefix():
    """Each product line declares its batch-ID letter; the year comes from the date."""
    assert [(line.product.master_record_id, line.prefix) for line in PRODUCT_LINES] == [
        ("MBR-SYN500", "B"),
        ("MBR-PLC250", "P"),
    ]


def test_product_without_defect_plan_gets_clean_batches(monkeypatch):
    """A product line with no defect plan only produces clean batches."""
    other = dataclasses.replace(SYN500, master_record_id="MBR-TST100")
    monkeypatch.setattr(
        cli_module, "PRODUCT_LINES", (ProductLine(other, "T", defect_plan=None),)
    )
    plans = plan_batches(4)
    assert [p.batch_id for p in plans] == [
        "T26-0001",
        "T26-0002",
        "T26-0003",
        "T26-0004",
    ]
    assert not any(p.defects for p in plans)


def test_three_batches_are_the_minimum_for_all_eight_rules():
    """B26-0002 carries the full tablet defect plan, so N=3 already covers all 8."""
    assert {f.rule_id for f in build_dataset(0, plan_batches(3)).findings} == set(
        RULE_IDS
    )
    assert build_dataset(0, plan_batches(2)).findings == []


def test_batch_id_year_matches_start_year():
    """Every ID's two-digit year is its start year, even at MAX_BATCHES (past 2026)."""
    plans = plan_batches(MAX_BATCHES)
    for plan in plans:
        assert plan.batch_id[1:3] == f"{plan.start:%y}"
    assert {p.start.year for p in plans} > {2026}


def test_max_batches_is_a_plain_sanity_limit():
    """The cap no longer depends on days left in 2026."""
    assert MAX_BATCHES == 9999


NEW_YEAR_START = datetime(2026, 12, 31, 7, 0)


def test_ids_restart_at_new_year():
    """A line's sequence restarts at 0001 when its start year changes."""
    plans = plan_batches(8, NEW_YEAR_START)
    assert ids(plans) == [
        "B26-0001",
        "P26-0001",
        "B26-0002",
        "P26-0002",
        "B27-0001",
        "P26-0003",
        "B27-0002",
        "P27-0001",
    ]
    starts = {p.batch_id: p.start for p in plans}
    assert starts["B27-0001"] == datetime(2027, 1, 1, 4, 40)
    assert starts["P26-0003"] == datetime(2026, 12, 31, 22, 40)
    assert starts["P27-0001"] == datetime(2027, 1, 1, 6, 30)


def test_defects_follow_line_position_across_new_year():
    """Defects go on a line's even positions, not its even sequence numbers.

    B27-0002 is the line's 4th batch, so it is defective even though its
    sequence number restarted at the new year.
    """
    plans = plan_batches(8, NEW_YEAR_START)
    assert [bool(p.defects) for p in plans] == [
        False,
        False,
        True,
        True,
        False,
        False,
        True,
        True,
    ]
    assert {p.batch_id: bool(p.defects) for p in plans}["B27-0002"]


def test_first_batch_of_each_line_starts_at_start():
    """Both lines' first batches start exactly at the given start."""
    plans = plan_batches(2, NEW_YEAR_START)
    assert [p.start for p in plans] == [NEW_YEAR_START, NEW_YEAR_START]


def test_unwritable_out_reports_error(tmp_path, capsys):
    """A filesystem problem gives a one-line error and exit code 1, not a traceback."""
    blocker = tmp_path / "not_a_folder"
    blocker.write_text("x")
    assert main(["--out", str(blocker)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("generate-batch-records: error: cannot write to ")
    assert "Traceback" not in err


# --- Product lines passed in (for --catalog) ----------------------------------

TST_LINE = ProductLine(
    dataclasses.replace(SYN500, master_record_id="MBR-TST100"), "T", defect_plan=None
)


def test_plan_batches_uses_given_lines():
    """Given lines alternate in order; each counts its own IDs from 0001."""
    plans = plan_batches(6, lines=(*PRODUCT_LINES, TST_LINE))
    assert ids(plans) == [
        "B26-0001",
        "P26-0001",
        "T26-0001",
        "B26-0002",
        "P26-0002",
        "T26-0002",
    ]
    assert not any(p.defects for p in plans if p.batch_id.startswith("T"))


def test_plan_batches_defaults_to_product_lines():
    """Without lines, plan_batches uses PRODUCT_LINES."""
    assert plan_batches(8) == plan_batches(8, lines=PRODUCT_LINES)


# --- Extra products from the catalog (--catalog) ------------------------------

CATALOG = Path(__file__).resolve().parents[2] / "catalog"
TABLET_PLAN = load_catalog(CATALOG).plans["tablet_standard"]


def catalog_product(base=SYN500, prefix="S", plan=None, **changes):
    """A CatalogProduct copied from a built-in product, as MBR-NEW100 unless changed."""
    config = dataclasses.replace(base, **{"master_record_id": "MBR-NEW100", **changes})
    return CatalogProduct(config, prefix, plan)


def test_extra_line_carries_the_catalog_plan():
    """A product's catalog plan becomes its line's defect plan, on its product."""
    item = catalog_product(plan=TABLET_PLAN)
    (line,) = extra_lines((item,))
    assert line.prefix == "S"
    assert line.product == item.product
    assert line.defect_plan.product == item.product
    assert line.defect_plan.defects == TABLET_PLAN.defects
    assert dict(line.defect_plan.comments) == dict(TABLET_PLAN.comments)


def test_extra_line_without_a_plan_is_clean_only():
    """A catalog product without a defect plan only gets clean batches."""
    (line,) = extra_lines((catalog_product(),))
    assert line.defect_plan is None


@pytest.mark.parametrize(
    ("products", "reason"),
    [
        ((catalog_product(prefix="B"),), "letter B"),
        (
            (catalog_product(), catalog_product(master_record_id="MBR-NEW200")),
            "letter S",
        ),
        (
            (catalog_product(master_record_id="MBR-SYN500", master_version="3"),),
            "MBR-SYN500 v3",
        ),
        (
            (catalog_product(), catalog_product(prefix="T")),
            "MBR-NEW100 v3",
        ),
        (
            (
                catalog_product(
                    plan=TABLET_PLAN,
                    limits={**SYN500.limits, "S-GRAN-01": ("", "20.0")},
                ),
            ),
            "S-GRAN-01",
        ),
    ],
    ids=[
        "builtin_letter",
        "duplicate_letter",
        "builtin_master",
        "duplicate_master",
        "plan_cannot_plant",
    ],
)
def test_extra_lines_refuses_clashes_and_unplantable_plans(products, reason):
    """Clashing letters or masters, and plans the limits can't take, are refused."""
    with pytest.raises(ValueError, match=reason):
        extra_lines(products)


def master_ids_in(out):
    """Distinct master_record_ids in out/master_record.csv, in order."""
    with (out / MASTER_FILE).open(newline="", encoding="utf-8") as f:
        return list(dict.fromkeys(r["master_record_id"] for r in csv.DictReader(f)))


def flagged_batches_in(out):
    """Batch IDs that have rows in out/expected_findings.csv."""
    with (out / EXPECTED_FILE).open(newline="", encoding="utf-8") as f:
        return {r["batch_id"] for r in csv.DictReader(f)}


def test_without_catalog_only_built_in_masters(tmp_path):
    """No --catalog flag: only the two built-in products are generated."""
    run(tmp_path, "--batches", "6")
    assert master_ids_in(tmp_path) == ["MBR-SYN500", "MBR-PLC250"]


def test_catalog_adds_its_products_after_the_built_ins(tmp_path):
    """--catalog adds one line per product file, in file-name order, after B and P."""
    run(tmp_path, "--batches", "11", "--catalog", str(CATALOG))
    assert master_ids_in(tmp_path) == [
        "MBR-SYN500",
        "MBR-PLC250",
        "MBR-EXL005",
        "MBR-FTP040",
        "MBR-MKZ075",
    ]
    assert batch_ids_in(tmp_path) == [
        "B26-0001",
        "P26-0001",
        "L26-0001",
        "F26-0001",
        "M26-0001",
        "B26-0002",
        "P26-0002",
        "L26-0002",
        "F26-0002",
        "M26-0002",
        "B26-0003",
    ]
    assert flagged_batches_in(tmp_path) == {
        "B26-0002",
        "P26-0002",
        "L26-0002",
        "F26-0002",
    }


def test_catalog_output_matches_the_rule_check(tmp_path):
    """For every batch, the oral liquid ones included, the rules find what's expected."""
    run(tmp_path, "--batches", "11", "--catalog", str(CATALOG))
    with (tmp_path / MASTER_FILE).open(newline="", encoding="utf-8") as f:
        masters = [MasterRow(**r) for r in csv.DictReader(f)]
    with (tmp_path / BATCH_FILE).open(newline="", encoding="utf-8") as f:
        batches = [BatchRow(**r) for r in csv.DictReader(f)]
    with (tmp_path / EXPECTED_FILE).open(newline="", encoding="utf-8") as f:
        expected = list(csv.DictReader(f))
    by_batch = {}
    for row in batches:
        by_batch.setdefault(row.batch_id, []).append(row)
    assert len(by_batch) == 11
    for batch_id, rows in by_batch.items():
        master = [m for m in masters if m.master_record_id == rows[0].master_record_id]
        found = sorted(
            tuple(reversed(f.split(":"))) for f in rule_violations(master, rows)
        )
        listed = sorted(
            (e["step_id"], e["rule_id"]) for e in expected if e["batch_id"] == batch_id
        )
        assert found == listed, batch_id


def test_same_catalog_gives_byte_identical_files(tmp_path):
    """The catalog is part of what fixes the output."""
    for name in ("a", "b"):
        run(tmp_path / name, "--batches", "6", "--catalog", str(CATALOG))
    assert files_of(tmp_path / "a") == files_of(tmp_path / "b")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('id_letter        = "F"', 'id_letter        = "B"'),
        ('"S-COMP-01"  = ["95", "105"]', '"S-COMP-01"  = [95, 105]'),
        (
            'defect_plan      = "tablet_standard"',
            'defect_plan      = "capsule_standard"',
        ),
    ],
    ids=["builtin_letter", "numeric_limit", "plan_for_other_template"],
)
def test_bad_catalog_is_a_usage_error(old, new, tmp_path, capsys):
    """An unusable catalog exits 2 with the reason, and writes nothing."""
    catalog = tmp_path / "catalog"
    shutil.copytree(CATALOG, catalog)
    product = catalog / "products" / "fictoprin_40mg.toml"
    text = product.read_text(encoding="utf-8")
    assert old in text
    product.write_text(text.replace(old, new), encoding="utf-8")
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as excinfo:
        run(out, "--catalog", str(catalog))
    assert excinfo.value.code == 2
    assert "argument --catalog" in capsys.readouterr().err
    assert not out.exists()
