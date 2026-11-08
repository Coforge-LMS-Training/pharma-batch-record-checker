"""Write a generated dataset as its three agreed CSV files, as one set.

master_record.csv, batch_record.csv and expected_findings.csv belong together:
expected findings describe exactly those batches. They are written to a staging
folder next to the output folder first and only then moved into place (each
move is atomic), so a failure never leaves new batches next to old expectations.
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

    The folder is created if needed; other files in it are left alone. If any
    file fails to write, out_dir keeps its previous files untouched, and the
    staging folder is always removed.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=out.parent, prefix=f".{out.name}-"))
    try:
        write_master_csv(dataset.masters, staging / MASTER_FILE)
        write_batch_csv(dataset.batches, staging / BATCH_FILE)
        write_expected_findings(dataset.findings, staging / EXPECTED_FILE)
        for name in DATASET_FILES:
            os.replace(staging / name, out / name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
