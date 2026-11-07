"""Write master_record.csv and batch_record.csv in the client-approved format.

Output matches the client samples: UTF-8, no BOM, CRLF ("\\r\\n") line
endings. The header is always written, in the exact client-approved order,
even when there are no rows. Cells are quoted only when needed (for example,
comments that contain commas, quotes or newlines). Reading CSVs is the
parser's job and lives elsewhere.

Values are written exactly as given and are never escaped. A comment that
starts with "=", "+", "-" or "@" stays as written, because comments are plain
data. If you open these files in a spreadsheet, import the columns as text so
nothing is evaluated as a formula.

Writing to a path is atomic: rows go to a temporary file in the same folder,
which replaces the destination only once every row is written. If a row fails,
any existing file is left untouched and no partial CSV is left behind.
"""

import csv
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

from pharma_batch_record_checker.schema import (
    BATCH_HEADER,
    MASTER_HEADER,
    BatchRow,
    MasterRow,
)

# A destination is either a filesystem path (str or path-like) or a text
# stream the caller has already opened.
Destination = str | os.PathLike[str] | TextIO


def write_master_csv(rows: Iterable[MasterRow], dest: Destination) -> None:
    """Write master record rows, with the header, to a path or open text file.

    An open file must be opened with newline="" (as the csv module requires),
    otherwise the platform may rewrite the CRLF line endings.
    """
    _write_csv(MASTER_HEADER, rows, dest)


def write_batch_csv(rows: Iterable[BatchRow], dest: Destination) -> None:
    """Write executed batch rows, with the header, to a path or open text file.

    An open file must be opened with newline="" (as the csv module requires),
    otherwise the platform may rewrite the CRLF line endings.
    """
    _write_csv(BATCH_HEADER, rows, dest)


def _write_csv(
    header: tuple[str, ...],
    rows: Iterable[MasterRow] | Iterable[BatchRow],
    dest: Destination,
) -> None:
    """Write to a path atomically, or straight into an already-open stream."""
    if isinstance(dest, (str, os.PathLike)):
        _write_path_atomically(header, rows, Path(dest))
    else:
        _write_rows(header, rows, dest)


def _write_path_atomically(
    header: tuple[str, ...],
    rows: Iterable[MasterRow] | Iterable[BatchRow],
    path: Path,
) -> None:
    """Write to a temp file beside path, then rename it over path in one step.

    - A symlinked path is resolved first, so its target is updated and the
      link itself is kept.
    - The data is fsynced before the rename, so a crash can't leave an empty
      or truncated file in place of the old one.
    - On any failure (a row, chmod or the rename), the temp file is removed.
    - The new file keeps the existing file's permissions. A brand-new file
      gets 0o666 minus the umask, exactly what a plain open() would give
      (a bare temp file would otherwise always be owner-only, 0o600).
    """
    path = path.resolve()
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o666 & ~_current_umask()
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            _write_rows(header, rows, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def _current_umask() -> int:
    """Return the process umask.

    The OS only exposes the umask by setting it, so this sets it to 0 and
    immediately restores it. The umask is briefly changed process-wide, which
    is fine for this single-threaded CLI.
    """
    umask = os.umask(0)
    os.umask(umask)
    return umask


def _write_rows(
    header: tuple[str, ...],
    rows: Iterable[MasterRow] | Iterable[BatchRow],
    f: TextIO,
) -> None:
    """Emit the header and each row's CSV cells through a csv.DictWriter."""
    writer = csv.DictWriter(
        f,
        fieldnames=header,
        lineterminator="\r\n",  # CRLF, as in the client samples
        quoting=csv.QUOTE_MINIMAL,
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row.to_csv_row())
