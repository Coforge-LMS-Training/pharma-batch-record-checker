"""Write a generated dataset as its three agreed CSV files, as one set.

master_record.csv, batch_record.csv and expected_findings.csv belong together:
expected findings describe exactly those batches. All three are first written
to a staging folder inside the output folder (same filesystem, and only the
output folder needs to be writable). Then each previous file is set aside and
the new one moved in. If any step fails, every previous file is put back, so an
error never leaves new batches next to old expectations. (Only a hard crash
between the moves themselves could; the moves are renames in one folder.)
"""

import os
import shutil
import tempfile
from pathlib import Path

from pharma_batch_record_checker.csv_io import write_batch_csv, write_master_csv
from pharma_batch_record_checker.generator.defects import Dataset
from pharma_batch_record_checker.generator.expected_findings import (
    write_expected_findings,
)

MASTER_FILE = "master_record.csv"
BATCH_FILE = "batch_record.csv"
EXPECTED_FILE = "expected_findings.csv"
DATASET_FILES: tuple[str, ...] = (MASTER_FILE, BATCH_FILE, EXPECTED_FILE)


def write_dataset(dataset: Dataset, out_dir: str | Path) -> None:
    """Write the dataset's three CSV files to out_dir, replacing them as a set.

    The folder is created if needed; other files in it are left alone. If
    writing or moving any file fails, out_dir keeps its previous files, and the
    staging folder is always removed.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=out, prefix=".staging-"))
    try:
        write_master_csv(dataset.masters, staging / MASTER_FILE)
        write_batch_csv(dataset.batches, staging / BATCH_FILE)
        write_expected_findings(dataset.findings, staging / EXPECTED_FILE)
        _swap_in(staging, out)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _swap_in(staging: Path, out: Path) -> None:
    """Move the staged files into out; on any failure put the previous ones back."""
    backup = staging / ".old"
    backup.mkdir()
    placed: list[str] = []
    try:
        for name in DATASET_FILES:
            if (out / name).exists():
                os.replace(out / name, backup / name)
            os.replace(staging / name, out / name)
            placed.append(name)
    except BaseException:
        for name in DATASET_FILES:
            if (backup / name).exists():
                os.replace(backup / name, out / name)
            elif name in placed:
                (out / name).unlink(missing_ok=True)
        raise
