"""Golden tests: a few synthetic rows in the exact shape of the client samples.

The client sample CSVs (sample/, not committed) use CRLF line endings, no BOM,
minute-precision timestamps and no quoting. Parsing these rows and writing them
back must reproduce the input byte for byte.
"""

import csv
import io

from pharma_batch_record_checker.csv_io import write_batch_csv, write_master_csv
from pharma_batch_record_checker.schema import BatchRow, MasterRow

MASTER_SAMPLE = (
    b"master_record_id,master_version,product_name,batch_size,batch_size_unit,"
    b"step_seq,step_id,unit_procedure,operation,phase,parameter,unit,"
    b"min_value,max_value,critical\r\n"
    b"MBR-TST100,2,Testolin 100 mg tablets,50000,tablets,1,S-DISP-01,Dispensing,"
    b"Weigh raw materials,Weigh API,API net weight,kg,149.25,150.75,Y\r\n"
    b"MBR-TST100,2,Testolin 100 mg tablets,50000,tablets,2,S-PACK-01,Packaging,"
    b"Line setup,Line clearance,,,,,Y\r\n"
    b"MBR-TST100,2,Testolin 100 mg tablets,50000,tablets,3,S-BLEND-02,Blending,"
    b"Pre-blend,Check dryness,Loss on drying,%,,3.0,Y\r\n"
    b"MBR-TST100,2,Testolin 100 mg tablets,50000,tablets,4,S-PACK-02,Packaging,"
    b"Bottle filling,Check count,Tablets per bottle,count,30,30,N\r\n"
)

BATCH_SAMPLE = (
    b"batch_id,master_record_id,master_version,step_id,reading,unit,"
    b"performed_by,performed_at,verified_by,verified_at,comment\r\n"
    b"T26-0001,MBR-TST100,2,S-DISP-01,150.02,kg,OP-201,2026-10-12T07:00,"
    b"OP-231,2026-10-12T07:10,\r\n"
    b"T26-0001,MBR-TST100,2,S-BLEND-02,20.00,%,OP-217,2026-10-12T07:30,,,\r\n"
    b"T26-0001,MBR-TST100,2,S-PACK-01,,,OP-222,2026-10-12T14:00,"
    b"OP-258,2026-10-12T14:10,Line clear. Previous product removed.\r\n"
    b"T26-0002,MBR-TST100,2,S-DISP-01,150.02,kg,OP-201,2026-10-13T10:45,"
    b"OP-246,2026-10-13T10:30,\r\n"
    b"T26-0002,MBR-TST100,2,S-PACK-02,31,count,OP-201,2026-10-13T11:30,"
    b"OP-231,2026-10-13T11:40,Count checked by supervisor. No review needed.\r\n"
)


def parse(data: bytes) -> list[dict[str, str]]:
    """Parse CSV bytes into row dicts, the way a CSV consumer would read them."""
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")))


def test_master_sample_rows_round_trip_byte_for_byte(tmp_path):
    """Sample-shaped master rows validate and are written back byte for byte."""
    rows = [MasterRow(**r) for r in parse(MASTER_SAMPLE)]
    path = tmp_path / "master_record.csv"
    write_master_csv(rows, path)
    assert path.read_bytes() == MASTER_SAMPLE


def test_batch_sample_rows_round_trip_byte_for_byte(tmp_path):
    """Sample-shaped batch rows, defects included, are written back byte for byte."""
    rows = [BatchRow(**r) for r in parse(BATCH_SAMPLE)]
    path = tmp_path / "batch_record.csv"
    write_batch_csv(rows, path)
    assert path.read_bytes() == BATCH_SAMPLE
