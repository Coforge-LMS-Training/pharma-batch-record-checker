"""The approved client sample, rebuilt from our own generator (PHA-39).

Four batches with the client's approved IDs: clean B26-0412 and P26-0201, and
defective B26-0413 (9 flagged steps) and P26-0202 (4 flagged steps). The same
steps break the same rules as in the client's sample, but every reading and
operator ID comes from our own seeded generator, so nothing is copied from the
client's file. Expected findings are recorded while the defects are planted.

The batch IDs and products are the approved synthetic identifiers listed in
PHA-39 (Synthetol/Placeprol are fictional); no real client data is used.

The committed copy lives in fixtures/approved_sample/ and is regenerated with
`write_approved_sample("fixtures/approved_sample")`; never edit it by hand.
"""

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from pharma_batch_record_checker.csv_io import write_batch_csv, write_master_csv
from pharma_batch_record_checker.generator.defects import (
    INSTRUCTION_COMMENTS,
    BatchPlan,
    Dataset,
    Defect,
    build_dataset,
)
from pharma_batch_record_checker.generator.expected_findings import (
    write_expected_findings,
)
from pharma_batch_record_checker.generator.masters import PLC250, SYN500

# Fixed, so the committed fixtures are reproducible byte for byte.
APPROVED_SAMPLE_SEED = 0

APPROVED_SAMPLE_PLANS: tuple[BatchPlan, ...] = (
    BatchPlan(SYN500, "B26-0412", datetime(2026, 10, 12, 7, 0)),
    BatchPlan(
        SYN500,
        "B26-0413",
        datetime(2026, 10, 13, 7, 0),
        defects=(
            Defect("S-DISP-01", "missing_second_check"),
            Defect("S-DISP-02", "missing_performer"),
            Defect("S-GRAN-03", "missing_reading"),
            Defect("S-GRAN-04", "unit_mismatch", unit="degF"),
            Defect("S-BLEND-01", "verifier_before_performer"),
            Defect("S-COMP-01", "out_of_range"),
            Defect("S-COMP-03", "unit_mismatch", unit="blank"),
            Defect("S-COAT-02", "out_of_range"),
            Defect("S-COAT-02", "same_person"),
            Defect("S-PACK-02", "missing_step"),
        ),
        comments={"S-COMP-01": INSTRUCTION_COMMENTS[1]},
    ),
    BatchPlan(PLC250, "P26-0201", datetime(2026, 10, 14, 7, 0)),
    BatchPlan(
        PLC250,
        "P26-0202",
        datetime(2026, 10, 15, 7, 0),
        defects=(
            Defect("S-BLEND-01", "out_of_range", side="low"),
            Defect("S-ENC-02", "same_person"),
            Defect("S-ENC-03", "out_of_range"),
            Defect("S-PACK-02", "out_of_range"),
        ),
        comments={"S-PACK-02": INSTRUCTION_COMMENTS[2]},
    ),
)

MASTER_FILE = "master_record.csv"
BATCH_FILE = "batch_record.csv"
EXPECTED_FILE = "expected_findings.csv"


def build_approved_sample() -> Dataset:
    """Masters, the four approved sample batches and their expected findings."""
    return build_dataset(APPROVED_SAMPLE_SEED, APPROVED_SAMPLE_PLANS)


def write_approved_sample(out_dir: str | Path) -> None:
    """Write master_record.csv, batch_record.csv and expected_findings.csv to out_dir.

    The three files are a set: they are all written to a staging folder next to
    out_dir first, and only then moved into place (each move is atomic). If
    building or writing fails, out_dir keeps its previous files untouched, so
    batches can't end up next to stale expected findings. The folder is created
    if needed, and the staging folder is always removed.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sample = build_approved_sample()
    staging = Path(tempfile.mkdtemp(dir=out.parent, prefix=f".{out.name}-"))
    try:
        write_master_csv(sample.masters, staging / MASTER_FILE)
        write_batch_csv(sample.batches, staging / BATCH_FILE)
        write_expected_findings(sample.findings, staging / EXPECTED_FILE)
        for name in (MASTER_FILE, BATCH_FILE, EXPECTED_FILE):
            os.replace(staging / name, out / name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
