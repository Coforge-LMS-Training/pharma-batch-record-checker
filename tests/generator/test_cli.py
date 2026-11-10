"""The seeded generator command, generate-batch-records (PHA-38)."""

import csv
import dataclasses
import subprocess
import sys
from datetime import datetime
from itertools import pairwise

import pytest

import pharma_batch_record_checker.generator.cli as cli_module
from pharma_batch_record_checker.generator.cli import (
    CHANGEOVER,
    MAX_BATCHES,
    PRODUCT_LINES,
    ProductLine,
    batch_span,
    main,
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
from pharma_batch_record_checker.schema import BATCH_HEADER, MASTER_HEADER
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
    """N must be 1 to MAX_BATCHES (so every batch starts in 2026)."""
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
    """Each product line declares its batch-ID prefix explicitly."""
    assert [(line.product.master_record_id, line.prefix) for line in PRODUCT_LINES] == [
        ("MBR-SYN500", "B26"),
        ("MBR-PLC250", "P26"),
    ]


def test_product_without_defect_plan_gets_clean_batches(monkeypatch):
    """A product line with no defect plan only produces clean batches."""
    other = dataclasses.replace(SYN500, master_record_id="MBR-TST100")
    monkeypatch.setattr(
        cli_module, "PRODUCT_LINES", (ProductLine(other, "T26", defect_plan=None),)
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


def test_all_start_dates_stay_in_2026():
    """Even at the maximum, every batch starts in 2026, matching the '26' in its ID."""
    plans = plan_batches(MAX_BATCHES)
    assert {p.start.year for p in plans} == {2026}


def test_unwritable_out_reports_error(tmp_path, capsys):
    """A filesystem problem gives a one-line error and exit code 1, not a traceback."""
    blocker = tmp_path / "not_a_folder"
    blocker.write_text("x")
    assert main(["--out", str(blocker)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("generate-batch-records: error: cannot write to ")
    assert "Traceback" not in err
