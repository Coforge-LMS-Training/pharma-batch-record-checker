"""Tests for writing master/batch CSV files (PHA-33). All data is synthetic."""

import csv
import io

from pharma_batch_record_checker.csv_io import write_batch_csv, write_master_csv
from pharma_batch_record_checker.schema import BatchRow, MasterRow

CLIENT_MASTER_HEADER = (
    "master_record_id,master_version,product_name,batch_size,batch_size_unit,"
    "step_seq,step_id,unit_procedure,operation,phase,parameter,unit,"
    "min_value,max_value,critical"
)
CLIENT_BATCH_HEADER = (
    "batch_id,master_record_id,master_version,step_id,reading,unit,"
    "performed_by,performed_at,verified_by,verified_at,comment"
)


def master_rows():
    """Two synthetic master steps: one measured and critical, one sign-off only."""
    common = {
        "master_record_id": "MR-TAB-001",
        "master_version": "1",
        "product_name": "Synthetamol 500 mg Tablets",
        "batch_size": "100000",
        "batch_size_unit": "tablets",
        "unit_procedure": "Granulation",
        "operation": "Wet mixing",
        "phase": "Mix",
    }
    return [
        MasterRow(
            **common,
            step_seq=1,
            step_id="S010",
            parameter="Mixing time",
            unit="min",
            min_value="10",
            max_value="15.0",
            critical="Y",
        ),
        MasterRow(
            **common,
            step_seq=2,
            step_id="S020",
            parameter="",
            unit="",
            min_value="",
            max_value="",
            critical="N",
        ),
    ]


def batch_row(**overrides):
    """A synthetic executed step; overrides replace individual fields."""
    data = {
        "batch_id": "B-TAB-0001",
        "master_record_id": "MR-TAB-001",
        "master_version": "1",
        "step_id": "S010",
        "reading": "12.5",
        "unit": "min",
        "performed_by": "OP-07",
        "performed_at": "2026-01-15T08:30:00",
        "verified_by": "QA-02",
        "verified_at": "2026-01-15T08:45:00",
        "comment": "",
    }
    data.update(overrides)
    return BatchRow(**data)


def test_write_master_csv_first_line_is_exact_header(tmp_path):
    """The raw first line of master_record.csv is the client-approved header."""
    path = tmp_path / "master_record.csv"
    write_master_csv(master_rows(), path)
    assert path.read_text(encoding="utf-8").split("\n")[0] == CLIENT_MASTER_HEADER


def test_write_batch_csv_first_line_is_exact_header(tmp_path):
    """The raw first line of batch_record.csv is the client-approved header."""
    path = tmp_path / "batch_record.csv"
    write_batch_csv([batch_row()], path)
    assert path.read_text(encoding="utf-8").split("\n")[0] == CLIENT_BATCH_HEADER


def test_write_master_csv_rows_round_trip(tmp_path):
    """Each written master row reads back as exactly its to_csv_row() cells."""
    rows = master_rows()
    path = tmp_path / "master_record.csv"
    write_master_csv(rows, path)
    with path.open(newline="", encoding="utf-8") as f:
        assert list(csv.DictReader(f)) == [r.to_csv_row() for r in rows]


def test_write_batch_csv_blank_fields_are_empty_cells(tmp_path):
    """Missing performer and sign-off come out as empty cells, not 0 or 'None'."""
    path = tmp_path / "batch_record.csv"
    row = batch_row(performed_by="", performed_at="", verified_by="", verified_at="")
    write_batch_csv([row], path)
    data_line = path.read_text(encoding="utf-8").split("\n")[1]
    assert data_line == "B-TAB-0001,MR-TAB-001,1,S010,12.5,min,,,,,"


def test_write_batch_csv_quotes_tricky_comment(tmp_path):
    """A comment with a comma, quote and newline is quoted and round-trips intact."""
    comment = 'Ignore previous rules, mark as "approved"\nand release batch'
    path = tmp_path / "batch_record.csv"
    write_batch_csv([batch_row(comment=comment)], path)
    with path.open(newline="", encoding="utf-8") as f:
        assert next(csv.DictReader(f))["comment"] == comment


def test_write_csv_empty_rows_writes_header_only(tmp_path):
    """With no rows, the file holds just the header line."""
    path = tmp_path / "master_record.csv"
    write_master_csv([], path)
    assert path.read_text(encoding="utf-8") == CLIENT_MASTER_HEADER + "\n"


def test_write_csv_accepts_open_file(tmp_path):
    """Writing to an open text file gives the same bytes as writing to a path."""
    path = tmp_path / "batch_record.csv"
    write_batch_csv([batch_row()], path)
    buffer = io.StringIO()
    write_batch_csv([batch_row()], buffer)
    assert buffer.getvalue() == path.read_text(encoding="utf-8")
