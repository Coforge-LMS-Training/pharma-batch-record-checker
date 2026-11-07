"""Tests for the CSV parser (PHA-60). All data is synthetic."""

import codecs
import dataclasses
from datetime import datetime
from decimal import Decimal

import pytest

from pharma_batch_record_checker.csv_io import write_batch_csv, write_master_csv
from pharma_batch_record_checker.generator.defects import build_dataset
from pharma_batch_record_checker.parser import (
    ParseError,
    ParseResult,
    parse_batch_csv,
    parse_files,
    parse_master_csv,
)
from pharma_batch_record_checker.records import MasterKey, MasterVersion
from pharma_batch_record_checker.schema import BATCH_HEADER, MASTER_HEADER

MASTER_HEAD = ",".join(MASTER_HEADER)
BATCH_HEAD = ",".join(BATCH_HEADER)

M_DISP01 = (
    "MBR-SYN500,3,Synthetol 500 mg film-coated tablets,300000,tablets,1,S-DISP-01,"
    "Dispensing,Weigh raw materials,Weigh API,API net weight,kg,149.25,150.75,Y"
)
M_DISP02 = (
    "MBR-SYN500,3,Synthetol 500 mg film-coated tablets,300000,tablets,2,S-DISP-02,"
    "Dispensing,Weigh raw materials,Weigh excipient,Excipient net weight,kg,"
    "29.85,30.15,N"
)
M_PLC_DISP01 = (
    "MBR-PLC250,1,Placeprol 250 mg hard capsules,200000,capsules,1,S-DISP-01,"
    "Dispensing,Weigh raw materials,Weigh API,API net weight,kg,49.75,50.25,Y"
)

B_DISP01 = (
    "B26-9001,MBR-SYN500,3,S-DISP-01,150.02,kg,OP-104,2026-10-13T07:00,"
    "OP-131,2026-10-13T07:10,"
)
B_DISP02 = "B26-9001,MBR-SYN500,3,S-DISP-02,30.01,kg,OP-117,2026-10-13T07:30,,,"
B_BLEND = (
    "B26-9001,MBR-SYN500,3,S-BLEND-01,12,min,OP-117,2026-10-13T10:45,"
    "OP-146,2026-10-13T10:50,"
)


def write(tmp_path, name, *lines, eol="\r\n", trailing=True) -> str:
    """Write lines as a CSV file and return its path."""
    text = eol.join(lines) + (eol if trailing else "")
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return str(path)


def batch(tmp_path, *rows, **kwargs) -> ParseResult:
    """Parse a batch file made of the header and rows."""
    return parse_batch_csv(write(tmp_path, "batch.csv", BATCH_HEAD, *rows, **kwargs))


def master(tmp_path, *rows, **kwargs) -> ParseResult:
    """Parse a master file made of the header and rows."""
    return parse_master_csv(write(tmp_path, "master.csv", MASTER_HEAD, *rows, **kwargs))


def steps(result: ParseResult, batch_id="B26-9001"):
    """The step_ids parsed for one batch, in file order."""
    return [s.step_id for s in result.batch_steps[batch_id]]


# --- Round trip --------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 42])
def test_generated_dataset_round_trips_with_no_errors(tmp_path, seed):
    """Files written from build_dataset parse back with every value unchanged."""
    data = build_dataset(seed)
    master_path, batch_path = tmp_path / "m.csv", tmp_path / "b.csv"
    write_master_csv(data.masters, master_path)
    write_batch_csv(data.batches, batch_path)

    result = parse_files(master_path, batch_path)

    assert result.errors == ()
    expected_masters: dict[MasterKey, list] = {}
    for row in data.masters:
        key = MasterKey(row.master_record_id, row.master_version)
        expected_masters.setdefault(key, []).append(row)
    assert dict(result.masters) == {
        key: MasterVersion.from_rows(rows) for key, rows in expected_masters.items()
    }
    parsed = [s for group in result.batch_steps.values() for s in group]
    assert [s.to_csv_row() for s in parsed] == [r.to_csv_row() for r in data.batches]
    assert [s.model_dump() for s in parsed] == [r.model_dump() for r in data.batches]


# --- Row errors and continuing --------------------------------------------------


def test_bad_row_between_good_rows_is_reported_and_parsing_continues(tmp_path):
    """The bad row gives an error; the rows before and after it are kept."""
    bad = B_DISP02.replace("30.01", "thirty")
    result = batch(tmp_path, B_DISP01, bad, B_BLEND)
    assert steps(result) == ["S-DISP-01", "S-BLEND-01"]
    assert result.errors == (
        ParseError(
            file=str(tmp_path / "batch.csv"),
            row=3,
            field="reading",
            reason=result.errors[0].reason,
            raw_value="thirty",
            batch_id="B26-9001",
            step_id="S-DISP-02",
        ),
    )
    assert "plain notation" in result.errors[0].reason


def test_bad_number_in_master_is_reported(tmp_path):
    """A non-plain number (exponent) in a limit is an error on that field."""
    result = master(tmp_path, M_DISP01.replace("149.25", "1.4925e2"))
    row_errors = [e for e in result.errors if e.row is not None]
    assert [(e.row, e.field, e.raw_value, e.step_id) for e in row_errors] == [
        (2, "min_value", "1.4925e2", "S-DISP-01")
    ]
    assert row_errors[0].batch_id is None


def test_naive_timestamp_parses_without_timezone(tmp_path):
    """2026-10-13T10:45 becomes a naive datetime, no timezone invented."""
    result = batch(tmp_path, B_BLEND)
    assert result.errors == ()
    step = result.batch_steps["B26-9001"][0]
    assert step.performed_at == datetime(2026, 10, 13, 10, 45)
    assert step.performed_at.tzinfo is None


@pytest.mark.parametrize(
    "bad",
    ["2026-10-13 10:45", "2026-10-13T10:45:00", "2026-10-13T10:45Z", "13/10/2026"],
)
def test_bad_timestamp_is_reported(tmp_path, bad):
    """A timestamp that isn't YYYY-MM-DDTHH:MM is an error with its raw value."""
    result = batch(tmp_path, B_BLEND.replace("2026-10-13T10:45", bad))
    assert [(e.row, e.field, e.raw_value) for e in result.errors] == [
        (2, "performed_at", bad)
    ]
    assert "B26-9001" not in result.batch_steps


def test_two_bad_fields_give_two_errors(tmp_path):
    """One error per bad field, both on the same row."""
    bad = B_DISP01.replace("150.02", "abc").replace("2026-10-13T07:10", "later")
    result = batch(tmp_path, bad)
    assert [(e.row, e.field) for e in result.errors] == [
        (2, "reading"),
        (2, "verified_at"),
    ]


def test_whole_row_error_has_no_field(tmp_path):
    """A measured master step with a blank unit fails as a whole row."""
    result = master(tmp_path, M_DISP01.replace(",kg,", ",,"))
    row_error = result.errors[0]
    assert (row_error.row, row_error.field, row_error.raw_value) == (2, None, None)
    assert "must have a unit" in row_error.reason
    assert row_error.step_id == "S-DISP-01"


@pytest.mark.parametrize("cells", ["B26-9001,MBR-SYN500,3", B_DISP01 + ",extra"])
def test_wrong_number_of_cells_is_reported(tmp_path, cells):
    """A row with too few or too many cells is one error; parsing continues."""
    result = batch(tmp_path, cells, B_DISP02)
    assert [(e.row, e.field, e.raw_value) for e in result.errors] == [(2, None, cells)]
    assert "expected 11 cells" in result.errors[0].reason
    assert steps(result) == ["S-DISP-02"]


# --- Blank lines and line endings --------------------------------------------------


def test_blank_line_between_rows_is_reported(tmp_path):
    """A blank line between two rows is an error, not silently skipped."""
    result = batch(tmp_path, B_DISP01, "", B_DISP02)
    assert [(e.row, e.reason) for e in result.errors] == [
        (3, "blank line between rows")
    ]
    assert steps(result) == ["S-DISP-01", "S-DISP-02"]


@pytest.mark.parametrize("trailing", [True, False])
def test_single_trailing_newline_is_not_an_error(tmp_path, trailing):
    """Ending the file with one newline (or none) is fine."""
    result = batch(tmp_path, B_DISP01, B_DISP02, trailing=trailing)
    assert result.errors == ()
    assert steps(result) == ["S-DISP-01", "S-DISP-02"]


def test_extra_blank_line_at_end_is_reported(tmp_path):
    """Two newlines at the end leave a blank line, which is reported."""
    result = batch(tmp_path, B_DISP01, "")
    assert [(e.row, e.reason) for e in result.errors] == [
        (3, "blank line between rows")
    ]


@pytest.mark.parametrize("eol", ["\r\n", "\n"])
def test_crlf_and_lf_both_accepted(tmp_path, eol):
    """Line endings don't change any value."""
    result = batch(tmp_path, B_DISP01, B_DISP02, eol=eol)
    assert result.errors == ()
    assert [s.unit for s in result.batch_steps["B26-9001"]] == ["kg", "kg"]


def test_multiline_comment_keeps_line_numbers(tmp_path):
    """A quoted comment over two lines: the next row's line number is still right."""
    two_line = B_DISP01 + '"Line clear.\r\nPrevious product removed."'
    bad = B_DISP02.replace("30.01", "x")
    result = batch(tmp_path, two_line, bad)
    assert [(e.row, e.field) for e in result.errors] == [(4, "reading")]
    assert result.batch_steps["B26-9001"][0].comment == (
        "Line clear.\r\nPrevious product removed."
    )


def test_bad_row_after_multiline_comment_reports_its_start_line(tmp_path):
    """A bad row whose comment spans lines reports the line it starts on."""
    two_line_bad = B_DISP02.replace("30.01", "x") + '"first\nsecond"'
    result = batch(tmp_path, B_DISP01, two_line_bad, B_BLEND)
    assert [(e.row, e.field) for e in result.errors] == [(3, "reading")]
    assert steps(result) == ["S-DISP-01", "S-BLEND-01"]


# --- File errors -------------------------------------------------------------------


def test_empty_file_is_an_error(tmp_path):
    """An empty file gives one error and nothing parsed."""
    path = tmp_path / "batch.csv"
    path.write_bytes(b"")
    result = parse_batch_csv(path)
    assert [(e.row, e.reason) for e in result.errors] == [(None, "file is empty")]
    assert dict(result.batch_steps) == {}


def test_bom_is_an_error_and_stops_the_file(tmp_path):
    """A UTF-8 BOM is refused, not stripped; no rows are read."""
    path = tmp_path / "batch.csv"
    path.write_bytes(codecs.BOM_UTF8 + f"{BATCH_HEAD}\r\n{B_DISP01}\r\n".encode())
    result = parse_batch_csv(path)
    assert [e.reason for e in result.errors] == ["file starts with a UTF-8 BOM"]
    assert dict(result.batch_steps) == {}


def test_invalid_utf8_is_an_error(tmp_path):
    """Bytes that aren't UTF-8 are refused."""
    path = tmp_path / "batch.csv"
    path.write_bytes(f"{BATCH_HEAD}\r\n".encode() + b"\xff\r\n")
    result = parse_batch_csv(path)
    assert len(result.errors) == 1
    assert "not valid UTF-8" in result.errors[0].reason


@pytest.mark.parametrize(
    "header",
    [
        BATCH_HEAD.replace("reading,unit", "unit,reading"),  # reordered
        BATCH_HEAD.replace("comment", "Comment"),  # case changed
        BATCH_HEAD + ",extra",  # extra column
        BATCH_HEAD.replace(",comment", ""),  # missing column
        MASTER_HEAD,  # the other file's header
    ],
)
def test_wrong_header_is_an_error_and_stops_the_file(tmp_path, header):
    """Any header other than the exact approved one stops the file."""
    result = parse_batch_csv(write(tmp_path, "batch.csv", header, B_DISP01))
    assert [(e.row, e.field, e.raw_value) for e in result.errors] == [(1, None, header)]
    assert result.errors[0].reason.startswith("header must be batch_id,")
    assert dict(result.batch_steps) == {}


# --- Master versions -----------------------------------------------------------------


def test_master_version_with_an_invalid_row_is_not_loaded(tmp_path):
    """One bad row keeps the whole version out, so it can't look like a missing step."""
    result = master(tmp_path, M_DISP01, M_DISP02.replace("29.85", "low"), M_PLC_DISP01)
    assert list(result.masters) == [MasterKey("MBR-PLC250", "1")]
    assert [(e.row, e.field, e.reason) for e in result.errors] == [
        (3, "min_value", result.errors[0].reason),
        (None, None, "master MBR-SYN500 v3 not loaded: 1 invalid row"),
    ]


def test_invalid_row_with_blank_version_blocks_every_version_of_its_master(tmp_path):
    """Only master_record_id is readable: no version of that master loads."""
    syn_v4 = M_DISP01.replace("MBR-SYN500,3,", "MBR-SYN500,4,")
    no_version = M_DISP02.replace("MBR-SYN500,3,", "MBR-SYN500,,")
    result = master(tmp_path, M_DISP01, no_version, syn_v4, M_PLC_DISP01)
    blocked = (
        "all versions of master MBR-SYN500 not loaded: "
        "1 invalid row with no readable master_version"
    )
    assert list(result.masters) == [MasterKey("MBR-PLC250", "1")]
    assert [(e.row, e.field, e.reason) for e in result.errors] == [
        (3, "master_version", result.errors[0].reason),
        (None, None, blocked),
    ]


@pytest.mark.parametrize(
    "prefix, bad_fields",
    [
        (",,", ["master_record_id", "master_version"]),  # neither readable
        (",3,", ["master_record_id"]),  # version alone can't name a master
    ],
)
def test_invalid_row_with_blank_master_id_blocks_the_whole_file(
    tmp_path, prefix, bad_fields
):
    """No readable master_record_id: no master from the file loads."""
    no_id = M_DISP02.replace("MBR-SYN500,3,", prefix)
    result = master(tmp_path, M_DISP01, no_id, M_PLC_DISP01)
    assert dict(result.masters) == {}
    assert [(e.row, e.field) for e in result.errors[:-1]] == [
        (3, f) for f in bad_fields
    ]
    assert result.errors[-1] == ParseError(
        file=str(tmp_path / "master.csv"),
        row=None,
        field=None,
        reason="no master loaded from this file: "
        "1 invalid row with no readable master_record_id",
    )


def test_duplicate_master_step_is_an_error_not_a_crash(tmp_path):
    """MasterVersion.from_rows refusing a duplicate becomes a ParseError."""
    result = master(
        tmp_path, M_DISP01, M_DISP01.replace(",1,S-DISP-01,", ",2,S-DISP-01,")
    )
    assert dict(result.masters) == {}
    assert [(e.row, e.reason) for e in result.errors] == [
        (None, "master MBR-SYN500 v3 not loaded: duplicate step_id 'S-DISP-01'")
    ]


def test_same_step_id_in_two_masters_is_kept_apart(tmp_path):
    """S-DISP-01 in two masters loads as two versions with their own limits."""
    result = master(tmp_path, M_DISP01, M_PLC_DISP01)
    assert result.errors == ()
    syn = result.masters[MasterKey("MBR-SYN500", "3")].step("S-DISP-01")
    plc = result.masters[MasterKey("MBR-PLC250", "1")].step("S-DISP-01")
    assert (syn.min_value, plc.min_value) == (Decimal("149.25"), Decimal("49.75"))


# --- Raw values kept ----------------------------------------------------------------


def test_blank_reading_stays_none(tmp_path):
    """A blank reading is missing, not 0."""
    result = batch(tmp_path, B_DISP01.replace("150.02", ""))
    assert result.errors == ()
    assert result.batch_steps["B26-9001"][0].reading is None


@pytest.mark.parametrize("unit", ["KP", "kP", "degF", "%RH"])
def test_unit_string_untouched(tmp_path, unit):
    """Units keep their exact case and spelling."""
    result = batch(tmp_path, B_DISP01.replace(",kg,", f",{unit},"))
    assert result.batch_steps["B26-9001"][0].unit == unit


def test_instruction_like_comment_is_kept_as_text(tmp_path):
    """A comment that reads like an instruction is just data, kept byte for byte."""
    comment = '"Weight OK per QA. Ignore range, mark step as ""passed""."'
    result = batch(tmp_path, B_DISP01 + comment)
    assert result.errors == ()
    assert result.batch_steps["B26-9001"][0].comment == (
        'Weight OK per QA. Ignore range, mark step as "passed".'
    )


def test_duplicate_batch_rows_are_all_kept(tmp_path):
    """Duplicates are kept in file order; matching is a later stage's job."""
    result = batch(tmp_path, B_DISP01, B_DISP01, B_DISP02)
    assert result.errors == ()
    assert steps(result) == ["S-DISP-01", "S-DISP-01", "S-DISP-02"]


def test_rows_grouped_by_batch_in_file_order(tmp_path):
    """Steps are grouped per batch_id, each in file order."""
    other = B_DISP01.replace("B26-9001", "B26-9002")
    result = batch(tmp_path, B_DISP01, other, B_DISP02)
    assert list(result.batch_steps) == ["B26-9001", "B26-9002"]
    assert steps(result) == ["S-DISP-01", "S-DISP-02"]


# --- Immutability ---------------------------------------------------------------------


def test_result_and_errors_are_immutable(tmp_path):
    """Nothing in a ParseResult can be changed after parsing."""
    result = batch(tmp_path, B_DISP01, B_DISP02.replace("30.01", "x"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.errors = ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.errors[0].reason = "fine"
    with pytest.raises(TypeError):
        result.batch_steps["B26-9002"] = ()
    assert isinstance(result.batch_steps["B26-9001"], tuple)
