"""Write master_record.csv and batch_record.csv in the client-approved format.

Output is UTF-8 with "\\n" line endings. The header is always written, in the
exact client-approved order, even when there are no rows. Cells are quoted
only when needed (for example, comments that contain commas, quotes or
newlines). Reading CSVs is the parser's job and lives elsewhere.
"""

import csv
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

from pharma_batch_record_checker.schema import (
    BATCH_HEADER,
    MASTER_HEADER,
    BatchRow,
    MasterRow,
)


def write_master_csv(rows: Iterable[MasterRow], dest: Path | TextIO) -> None:
    """Write master record rows, with the header, to a path or open text file."""
    _write_csv(MASTER_HEADER, rows, dest)


def write_batch_csv(rows: Iterable[BatchRow], dest: Path | TextIO) -> None:
    """Write executed batch rows, with the header, to a path or open text file."""
    _write_csv(BATCH_HEADER, rows, dest)


def _write_csv(
    header: tuple[str, ...],
    rows: Iterable[MasterRow] | Iterable[BatchRow],
    dest: Path | TextIO,
) -> None:
    """Write header then rows to dest, opening (and closing) it if it is a path."""
    if isinstance(dest, Path):
        with dest.open("w", newline="", encoding="utf-8") as f:
            _write_rows(header, rows, f)
    else:
        _write_rows(header, rows, dest)


def _write_rows(
    header: tuple[str, ...],
    rows: Iterable[MasterRow] | Iterable[BatchRow],
    f: TextIO,
) -> None:
    """Emit the header and each row's CSV cells through a csv.DictWriter."""
    writer = csv.DictWriter(
        f,
        fieldnames=header,
        lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL,
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row.to_csv_row())
