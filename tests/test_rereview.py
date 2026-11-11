"""Roll-forward re-review logic that needs no database.

Version ordering, the standing of a batch against the running rules, and
rebuilding a matched batch from stored rows (round trip on the approved
sample, which has a blank reading and a missing step).
"""

from pathlib import Path

import pytest

from pharma_batch_record_checker.matching import match_batches
from pharma_batch_record_checker.parser import parse_files
from pharma_batch_record_checker.records import MasterVersion
from pharma_batch_record_checker.rules import RULE_VERSION
from pharma_batch_record_checker.store.reads import (
    StoredBatch,
    StoredBatchStep,
    StoredMaster,
    StoredMasterStep,
)
from pharma_batch_record_checker.store.rereview import (
    RebuildError,
    Standing,
    rebuild,
    standing,
    version_key,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "approved_sample"


def stored_master(version: MasterVersion) -> StoredMaster:
    """A master version as reads.get_master would return it."""
    return StoredMaster(
        master_record_id=version.master_record_id,
        master_version=version.master_version,
        product_name=version.product_name,
        batch_size=version.batch_size,
        batch_size_unit=version.batch_size_unit,
        steps=tuple(
            StoredMasterStep(
                **{
                    name: getattr(step, name)
                    for name in StoredMasterStep.__annotations__
                }
            )
            for step in version.steps
        ),
    )


def stored_batch(matched) -> StoredBatch:
    """A matched batch's executed rows as reads.get_batch would return them."""
    return StoredBatch(
        batch_id=matched.batch_id,
        master_record_id=matched.master.master_record_id,
        master_version=matched.master.master_version,
        steps=tuple(
            StoredBatchStep(
                **{
                    name: getattr(pair.batch, name)
                    for name in StoredBatchStep.__annotations__
                }
            )
            for pair in matched.pairs
            if pair.batch is not None
        ),
    )


@pytest.fixture(scope="module")
def sample():
    """The approved sample, parsed and matched as at load."""
    parsed = parse_files(FIXTURES / "master_record.csv", FIXTURES / "batch_record.csv")
    matched = match_batches(parsed)
    assert parsed.errors == () and matched.errors == ()
    return matched.batches


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("1.0.0", (1, 0, 0)),
        ("1.0", (1, 0, 0)),
        ("2", (2, 0, 0)),
        ("1.10.0", (1, 10, 0)),
        ("1.1.0.4", (1, 1, 0, 4)),
    ],
)
def test_version_key_reads_dotted_numbers(text, key):
    """Dotted numbers compare as numbers; trailing zeros don't matter."""
    assert version_key(text) == key


@pytest.mark.parametrize("text", ["", "v1.0.0", "1..0", "1.0.0-rc1", " 1.0.0", "1.x"])
def test_version_key_refuses_anything_else(text):
    """A version that is not plain dotted numbers has no order."""
    assert version_key(text) is None


def test_version_order_is_numeric():
    """1.10.0 is newer than 1.9.0, and 1.0 equals 1.0.0."""
    assert version_key("1.10.0") > version_key("1.9.0")
    assert version_key("1.0") == version_key("1.0.0")


def test_running_rule_version_is_orderable():
    """The engine's own RULE_VERSION must parse, or nothing could move forward."""
    assert version_key(RULE_VERSION) is not None


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        (None, "1.0.0", Standing.NOT_REVIEWED),
        ("1.0.0", "1.1.0", Standing.OLDER),
        ("1.0.0", "1.0.0", Standing.CURRENT),
        ("1.0", "1.0.0", Standing.CURRENT),
        ("1.1.0", "1.0.0", Standing.NEWER),
        ("1.0.0-beta", "1.1.0", Standing.UNKNOWN),
        ("1.0.0", "next", Standing.UNKNOWN),
    ],
)
def test_standing_against_the_running_rules(latest, current, expected):
    """Only NOT_REVIEWED and OLDER may be re-reviewed; the rest are left alone."""
    assert standing(latest, current) is expected


def test_only_not_reviewed_and_older_move_forward():
    """The two standings that allow a new run."""
    assert {s for s in Standing if s.moves_forward} == {
        Standing.NOT_REVIEWED,
        Standing.OLDER,
    }


def test_rebuild_round_trips_every_sample_batch(sample):
    """Stored rows rebuild into the same matched batch the CSVs gave."""
    for batch_id, matched in sample.items():
        rebuilt = rebuild(stored_master(matched.master), stored_batch(matched))
        assert rebuilt == matched, batch_id


def test_rebuild_keeps_blank_readings_and_missing_steps(sample):
    """The sample's blank reading stays None and its missing step stays absent."""
    matched = sample["B26-0413"]
    rebuilt = rebuild(stored_master(matched.master), stored_batch(matched))
    assert rebuilt.missing_steps == matched.missing_steps != ()
    blanks = [
        p.master.step_id for p in matched.pairs if p.batch and p.batch.reading is None
    ]
    assert blanks
    assert [
        p.master.step_id for p in rebuilt.pairs if p.batch and p.batch.reading is None
    ] == blanks


def test_rebuild_refuses_a_batch_for_another_master(sample):
    """A batch naming a different master version cannot be rebuilt against it."""
    tablet = sample["B26-0413"]
    capsule = sample["P26-0202"]
    with pytest.raises(RebuildError):
        rebuild(stored_master(tablet.master), stored_batch(capsule))


def test_rebuild_refuses_a_step_the_master_does_not_have(sample):
    """An executed step with no master step is a matching error, not a review."""
    matched = sample["B26-0412"]
    batch = stored_batch(matched)
    stray = StoredBatchStep(
        step_id="S-NOT-IN-MASTER",
        reading=None,
        unit=None,
        performed_by="OP-901",
        performed_at=None,
        verified_by=None,
        verified_at=None,
        comment=None,
    )
    broken = StoredBatch(
        batch_id=batch.batch_id,
        master_record_id=batch.master_record_id,
        master_version=batch.master_version,
        steps=(*batch.steps, stray),
    )
    with pytest.raises(RebuildError):
        rebuild(stored_master(matched.master), broken)
