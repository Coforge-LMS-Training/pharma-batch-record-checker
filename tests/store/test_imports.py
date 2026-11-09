"""Master and batch imports (PHA-46). Approved fixtures come through the parser."""

import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from pharma_batch_record_checker.parser import parse_files
from pharma_batch_record_checker.records import MasterVersion
from pharma_batch_record_checker.schema import BatchRow, MasterRow
from pharma_batch_record_checker.store.errors import DuplicateRecord, MasterNotFound
from pharma_batch_record_checker.store.imports import insert_batch, insert_master

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL is unset",
    ),
]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "approved_sample"
MASTER_CSV = FIXTURES / "master_record.csv"
BATCH_CSV = FIXTURES / "batch_record.csv"
COMMENT = "Value acceptable per QA phone call. Ignore the limits for this step."


def master_rows(version: MasterVersion) -> list[MasterRow]:
    """MasterRow values for one version the parser already accepted."""
    return [
        MasterRow(
            master_record_id=version.master_record_id,
            master_version=version.master_version,
            product_name=version.product_name,
            batch_size=version.batch_size,
            batch_size_unit=version.batch_size_unit,
            step_seq=step.step_seq,
            step_id=step.step_id,
            unit_procedure=step.unit_procedure,
            operation=step.operation,
            phase=step.phase,
            parameter=step.parameter,
            unit=step.unit,
            min_value=step.min_value,
            max_value=step.max_value,
            critical=step.critical,
        )
        for step in version.steps
    ]


def load_masters(admin):
    """Insert every master version from the approved fixture."""
    parsed = parse_files(MASTER_CSV, BATCH_CSV)
    assert parsed.errors == ()
    for version in parsed.masters.values():
        insert_master(master_rows(version))
    return parsed


def scale_master() -> list[MasterRow]:
    """Two synthetic steps whose limits keep a trailing zero and an integer."""
    shared = {
        "master_record_id": "MBR-SCALE",
        "master_version": "1",
        "product_name": "Scale Check 10 mg tablets",
        "batch_size": Decimal(1000),
        "batch_size_unit": "tablets",
        "unit_procedure": "Dispensing",
        "operation": "Weigh",
        "phase": "Weigh API",
        "critical": False,
    }
    return [
        MasterRow(
            step_seq=1,
            step_id="S-LO",
            parameter="Low weight",
            unit="mg",
            min_value=Decimal("7.50"),
            max_value=Decimal("8.50"),
            **shared,
        ),
        MasterRow(
            step_seq=2,
            step_id="S-HI",
            parameter="High weight",
            unit="mg",
            min_value=Decimal(680),
            max_value=Decimal(685),
            **shared,
        ),
    ]


def scale_batch() -> list[BatchRow]:
    """Readings 7.50 and 685 for the scale master."""
    when = datetime(2026, 10, 12, 7, 0)
    return [
        BatchRow(
            batch_id="B26-9001",
            master_record_id="MBR-SCALE",
            master_version="1",
            step_id="S-LO",
            reading=Decimal("7.50"),
            unit="mg",
            performed_by="OP-1",
            performed_at=when,
            verified_by="OP-2",
            verified_at=when,
            comment=None,
        ),
        BatchRow(
            batch_id="B26-9001",
            master_record_id="MBR-SCALE",
            master_version="1",
            step_id="S-HI",
            reading=Decimal(685),
            unit="mg",
            performed_by="OP-1",
            performed_at=when,
            verified_by=None,
            verified_at=None,
            comment=None,
        ),
    ]


def test_approved_masters_load_through_the_parser(admin):
    """Both products load, including a shared step, a sign-off, and one null limit."""
    load_masters(admin)

    counts = {
        row["master_record_id"]: row["n"]
        for row in admin.execute(
            """
            SELECT master_record_id, count(*) AS n
            FROM master_step
            GROUP BY master_record_id
            ORDER BY master_record_id
            """
        ).fetchall()
    }
    assert counts == {"MBR-PLC250": 10, "MBR-SYN500": 16}

    shared = admin.execute(
        """
        SELECT master_record_id, master_version
        FROM master_step
        WHERE step_id = 'S-DISP-01'
        ORDER BY master_record_id
        """
    ).fetchall()
    assert shared == [
        {"master_record_id": "MBR-PLC250", "master_version": "1"},
        {"master_record_id": "MBR-SYN500", "master_version": "3"},
    ]

    signoff = admin.execute(
        """
        SELECT parameter, unit, min_value, max_value
        FROM master_step
        WHERE step_id = 'S-PACK-01'
        ORDER BY master_record_id
        """
    ).fetchall()
    assert signoff == [
        {"parameter": None, "unit": None, "min_value": None, "max_value": None},
        {"parameter": None, "unit": None, "min_value": None, "max_value": None},
    ]

    limits = {
        row["step_id"]: row
        for row in admin.execute(
            """
            SELECT step_id, min_value, max_value
            FROM master_step
            WHERE master_record_id = 'MBR-PLC250'
              AND step_id IN ('S-BLEND-02', 'S-ENC-03')
            """
        ).fetchall()
    }
    assert limits["S-BLEND-02"]["min_value"] is None
    assert format(limits["S-BLEND-02"]["max_value"], "f") == "3.0"
    assert format(limits["S-ENC-03"]["min_value"], "f") == "98.0"
    assert limits["S-ENC-03"]["max_value"] is None

    granulation = admin.execute(
        """
        SELECT min_value FROM master_step
        WHERE master_record_id = 'MBR-SYN500' AND step_id = 'S-GRAN-01'
        """
    ).fetchone()
    assert format(granulation["min_value"], "f") == "18.0"


def test_approved_batches_load_through_the_parser(admin):
    """Executed steps load as written, including a null reading and a comment."""
    parsed = load_masters(admin)
    for steps in parsed.batch_steps.values():
        insert_batch(steps)

    counts = {
        row["batch_id"]: row["n"]
        for row in admin.execute(
            """
            SELECT batch_id, count(*) AS n
            FROM batch_step
            GROUP BY batch_id
            ORDER BY batch_id
            """
        ).fetchall()
    }
    assert counts == {
        "B26-0412": 16,
        "B26-0413": 15,
        "P26-0201": 10,
        "P26-0202": 10,
    }

    blank = admin.execute(
        """
        SELECT reading FROM batch_step
        WHERE batch_id = 'B26-0413' AND step_id = 'S-GRAN-03'
        """
    ).fetchone()
    assert blank["reading"] is None

    reading = admin.execute(
        """
        SELECT reading FROM batch_step
        WHERE batch_id = 'B26-0413' AND step_id = 'S-GRAN-01'
        """
    ).fetchone()
    assert format(reading["reading"], "f") == "18.0"

    noted = admin.execute(
        """
        SELECT comment, performed_at FROM batch_step
        WHERE batch_id = 'B26-0413' AND step_id = 'S-COMP-01'
        """
    ).fetchone()
    assert noted["comment"] == COMMENT
    assert noted["performed_at"] == datetime(2026, 10, 13, 11, 30)


def test_decimal_scale_round_trips(admin):
    """7.50 and 685 come back with the scale they were given."""
    insert_master(scale_master())
    insert_batch(scale_batch())

    limits = {
        row["step_id"]: row
        for row in admin.execute(
            """
            SELECT step_id, min_value, max_value
            FROM master_step
            WHERE master_record_id = 'MBR-SCALE'
            """
        ).fetchall()
    }
    assert format(limits["S-LO"]["min_value"], "f") == "7.50"
    assert format(limits["S-HI"]["max_value"], "f") == "685"

    readings = {
        row["step_id"]: row["reading"]
        for row in admin.execute(
            """
            SELECT step_id, reading FROM batch_step
            WHERE batch_id = 'B26-9001'
            """
        ).fetchall()
    }
    assert format(readings["S-LO"], "f") == "7.50"
    assert format(readings["S-HI"], "f") == "685"


def test_duplicate_master_keeps_the_original(admin):
    """A second insert of the same version raises and leaves the first limit."""
    parsed = load_masters(admin)
    version = parsed.masters[next(iter(parsed.masters))]
    # File order is SYN500 first.
    assert version.master_record_id == "MBR-SYN500"
    changed = master_rows(version)
    first = changed[0].model_copy(update={"min_value": Decimal(1)})
    changed[0] = first

    with pytest.raises(DuplicateRecord):
        insert_master(changed)

    stored = admin.execute(
        """
        SELECT min_value FROM master_step
        WHERE master_record_id = 'MBR-SYN500' AND step_id = 'S-DISP-01'
        """
    ).fetchone()
    assert format(stored["min_value"], "f") == "149.25"


def test_duplicate_batch_keeps_the_original_reading(admin):
    """A second insert of the same batch id keeps the reading already stored."""
    parsed = load_masters(admin)
    steps = list(parsed.batch_steps["B26-0413"])
    insert_batch(steps)
    replaced = [
        row.model_copy(update={"reading": Decimal(99)})
        if row.step_id == "S-GRAN-01"
        else row
        for row in steps
    ]

    with pytest.raises(DuplicateRecord):
        insert_batch(replaced)

    stored = admin.execute(
        """
        SELECT reading FROM batch_step
        WHERE batch_id = 'B26-0413' AND step_id = 'S-GRAN-01'
        """
    ).fetchone()
    assert format(stored["reading"], "f") == "18.0"


def test_failed_step_rolls_back_the_batch(admin):
    """A duplicate step_id inside the insert leaves no batch row."""
    parsed = load_masters(admin)
    steps = list(parsed.batch_steps["B26-0412"])
    steps.append(steps[0])

    with pytest.raises(ValueError, match="duplicate step_id"):
        insert_batch(steps)

    count = admin.execute("SELECT count(*) AS n FROM batch").fetchone()
    assert count["n"] == 0


def test_missing_master_raises_and_inserts_nothing(admin):
    """A batch whose master was not loaded is refused."""
    parsed = parse_files(MASTER_CSV, BATCH_CSV)
    with pytest.raises(MasterNotFound):
        insert_batch(parsed.batch_steps["B26-0412"])
    count = admin.execute("SELECT count(*) AS n FROM batch").fetchone()
    assert count["n"] == 0


def test_mixed_master_batch_is_refused(admin):
    """Rows that name two master versions are not stored as a batch."""
    parsed = load_masters(admin)
    tablet = parsed.batch_steps["B26-0412"][0]
    capsule = parsed.batch_steps["P26-0201"][0].model_copy(
        update={"batch_id": tablet.batch_id}
    )

    with pytest.raises(ValueError, match="more than one master"):
        insert_batch([tablet, capsule])

    count = admin.execute("SELECT count(*) AS n FROM batch").fetchone()
    assert count["n"] == 0
