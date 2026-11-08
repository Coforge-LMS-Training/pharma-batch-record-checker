"""Read master_record.csv and batch_record.csv into normalized records.

Values are validated only by the row models in schema.py (`MasterRow`,
`BatchRow`); this module adds no value rules of its own. It turns each problem
into a structured `ParseError` and keeps going, so one run reports every
problem and no row is ever silently dropped.

- File problems (empty file, UTF-8 BOM, invalid UTF-8, wrong header, broken
  CSV quoting) give one error and stop that file.
- Row problems (wrong number of cells, a blank line between rows, invalid
  values) give one error per bad field, or one for the whole row, then parsing
  continues with the next row. A single trailing newline is not a blank line.
- A master version is built only when no invalid row could belong to it;
  otherwise a "not loaded" error is recorded instead, so a malformed row can't
  look like a missing step (see `parse_master_csv`).
- Batch rows are all kept, in file order, duplicates included. Matching them
  to master steps is the next stage's job.

CRLF and LF line endings are both accepted. Valid values are kept exactly as
written: timestamps stay timezone-naive, units keep their case, a blank reading
is None (never 0), and comments are inert text.
"""

import codecs
import csv
import io
import os
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from pydantic import ValidationError

from pharma_batch_record_checker.records import BatchStep, MasterKey, MasterVersion
from pharma_batch_record_checker.schema import (
    BATCH_HEADER,
    MASTER_HEADER,
    BatchRow,
    CsvRow,
    MasterRow,
)

Source = str | os.PathLike[str]


@dataclass(frozen=True)
class ParseError:
    """One problem found while parsing a CSV file.

    row is the CSV line number (the header is line 1); for a row whose quoted
    comment spans several lines, it is the line the row starts on. row is None
    for a problem that belongs to a whole master version. field and raw_value
    are None when the problem is not about one cell. batch_id and step_id are
    the row's raw values when they could be read, so later stages can tell
    which batch or step a malformed row belonged to.
    """

    file: str
    row: int | None
    field: str | None
    reason: str
    raw_value: str | None = None
    batch_id: str | None = None
    step_id: str | None = None


@dataclass(frozen=True)
class ParseResult:
    """Everything parsed from the input files, plus every problem found.

    masters holds only fully valid master versions. batch_steps holds every
    valid batch row, grouped by batch_id in file order, duplicates included.
    batch_file is the batch file's path (as in ParseError.file), or None if
    no batch file was parsed, so errors from that file can be told apart.
    """

    masters: Mapping[MasterKey, MasterVersion] = field(
        default_factory=lambda: MappingProxyType({})
    )
    batch_steps: Mapping[str, tuple[BatchStep, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    errors: tuple[ParseError, ...] = ()
    batch_file: str | None = None


def parse_files(master_path: Source, batch_path: Source) -> ParseResult:
    """Parse a master file and a batch file into one result."""
    masters = parse_master_csv(master_path)
    batches = parse_batch_csv(batch_path)
    return ParseResult(
        masters=masters.masters,
        batch_steps=batches.batch_steps,
        errors=masters.errors + batches.errors,
        batch_file=batches.batch_file,
    )


def parse_master_csv(path: Source) -> ParseResult:
    """Parse master_record.csv into MasterVersions keyed by MasterKey.

    An invalid row blocks every version it might belong to, so no version
    loads with a step silently missing:
    - id and version readable: that version is not loaded;
    - only master_record_id readable: no version of that master is loaded;
    - master_record_id not readable: no master from this file is loaded.
    Each case adds a "not loaded" error.
    """
    name = os.fspath(path)
    errors: list[ParseError] = []
    rows: dict[MasterKey, list[MasterRow]] = {}
    bad_versions: Counter[MasterKey] = Counter()  # invalid rows, id + version
    bad_ids: Counter[str] = Counter()  # invalid rows, id but no version
    unattributed = 0  # invalid rows with no readable master_record_id

    for line, cells in _rows(name, MASTER_HEADER, errors):
        row = _validate(name, line, cells, MasterRow, errors)
        if row is not None:
            key = MasterKey(row.master_record_id, row.master_version)
            rows.setdefault(key, []).append(row)
            continue
        raw_id = _readable(cells, "master_record_id")
        raw_version = _readable(cells, "master_version")
        if raw_id is None:
            unattributed += 1
        elif raw_version is None:
            bad_ids[raw_id] += 1
        else:
            bad_versions[MasterKey(raw_id, raw_version)] += 1

    masters: dict[MasterKey, MasterVersion] = {}
    for key in dict.fromkeys([*rows, *bad_versions]):  # first-seen order
        label = f"master {key.master_record_id} v{key.master_version} not loaded"
        if key in bad_versions:
            reason = f"{label}: {_invalid_rows(bad_versions[key])}"
            errors.append(ParseError(name, None, None, reason))
            continue
        try:
            version = MasterVersion.from_rows(rows[key])
        except ValueError as exc:
            # from_rows already names the master; don't repeat it.
            detail = str(exc).removeprefix(
                f"master {key.master_record_id} v{key.master_version}: "
            )
            errors.append(ParseError(name, None, None, f"{label}: {detail}"))
            continue
        # A valid version is still held back by a wider error, reported below.
        if not unattributed and key.master_record_id not in bad_ids:
            masters[key] = version

    for master_id, count in bad_ids.items():
        reason = (
            f"all versions of master {master_id} not loaded: "
            f"{_invalid_rows(count)} with no readable master_version"
        )
        errors.append(ParseError(name, None, None, reason))
    if unattributed:
        reason = (
            "no master loaded from this file: "
            f"{_invalid_rows(unattributed)} with no readable master_record_id"
        )
        errors.append(ParseError(name, None, None, reason))
    return ParseResult(masters=MappingProxyType(masters), errors=tuple(errors))


def _invalid_rows(count: int) -> str:
    """'1 invalid row' or 'N invalid rows'."""
    return f"{count} invalid row{'' if count == 1 else 's'}"


def parse_batch_csv(path: Source) -> ParseResult:
    """Parse batch_record.csv into BatchSteps grouped by batch_id."""
    name = os.fspath(path)
    errors: list[ParseError] = []
    steps: dict[str, list[BatchStep]] = {}
    for line, cells in _rows(name, BATCH_HEADER, errors):
        row = _validate(name, line, cells, BatchRow, errors)
        if row is not None:
            steps.setdefault(row.batch_id, []).append(BatchStep.from_row(row))
    return ParseResult(
        batch_steps=MappingProxyType({b: tuple(s) for b, s in steps.items()}),
        errors=tuple(errors),
        batch_file=name,
    )


def _rows(
    name: str, header: tuple[str, ...], errors: list[ParseError]
) -> Iterator[tuple[int, dict[str, str]]]:
    """Yield (line number, {column: cell}) for each well-formed data row.

    File problems are appended to errors and end the file. Rows with the
    wrong number of cells and blank lines are appended to errors and skipped.
    """
    text = _decode(name, errors)
    if text is None:
        return
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        found = next(reader, None)
        if found is None or tuple(found) != header:
            errors.append(
                ParseError(
                    name,
                    1,
                    None,
                    f"header must be {','.join(header)}",
                    raw_value=None if found is None else ",".join(found),
                )
            )
            return
        end = reader.line_num
        for cells in reader:
            line, end = end + 1, reader.line_num
            if not cells:
                errors.append(ParseError(name, line, None, "blank line between rows"))
            elif len(cells) != len(header):
                errors.append(
                    ParseError(
                        name,
                        line,
                        None,
                        f"expected {len(header)} cells, found {len(cells)}",
                        raw_value=",".join(cells),
                    )
                )
            else:
                yield line, dict(zip(header, cells, strict=True))
    except csv.Error as exc:
        errors.append(ParseError(name, reader.line_num, None, f"invalid CSV: {exc}"))


def _decode(name: str, errors: list[ParseError]) -> str | None:
    """Read the file as UTF-8 text, or record why it can't be used."""
    with open(name, "rb") as f:
        data = f.read()
    reason = None
    if not data:
        reason = "file is empty"
    elif data.startswith(codecs.BOM_UTF8):
        reason = "file starts with a UTF-8 BOM"
    else:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            reason = f"file is not valid UTF-8 (byte {exc.start})"
    errors.append(ParseError(name, None, None, reason))
    return None


def _validate[R: CsvRow](
    name: str,
    line: int,
    cells: dict[str, str],
    model: type[R],
    errors: list[ParseError],
) -> R | None:
    """Validate one row with its schema model; record one error per bad field."""
    try:
        return model.model_validate(cells)
    except ValidationError as exc:
        reasons: dict[str | None, list[str]] = {}
        for err in exc.errors():
            loc = err["loc"]
            column = str(loc[0]) if loc else None
            reasons.setdefault(column, []).append(err["msg"])
        context = _context(cells)
        for column, msgs in reasons.items():
            errors.append(
                ParseError(
                    name,
                    line,
                    column,
                    "; ".join(msgs),
                    raw_value=cells.get(column) if column else None,
                    **context,
                )
            )
        return None


def _context(cells: dict[str, str]) -> dict[str, str | None]:
    """The row's batch_id and step_id, when readable."""
    return {
        "batch_id": _readable(cells, "batch_id"),
        "step_id": _readable(cells, "step_id"),
    }


def _readable(cells: dict[str, str], column: str) -> str | None:
    """The raw cell text, or None if the column is absent or blank."""
    value = cells.get(column)
    return value if value and value.strip() else None
