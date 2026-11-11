"""Re-reviewing stored batches with the running rules (forward only).

The approved sample is loaded once (with the engine's RULE_VERSION), then re-reviewed as
if newer or older rules were running, by passing current=. Synthetic rows only.
"""

import dataclasses
import os
from pathlib import Path

import pytest

from pharma_batch_record_checker import rules
from pharma_batch_record_checker.records import BatchStep
from pharma_batch_record_checker.schema import BatchRow
from pharma_batch_record_checker.store.hashchain import verify_chain
from pharma_batch_record_checker.store.imports import insert_batch
from pharma_batch_record_checker.store.load import load_and_review
from pharma_batch_record_checker.store.reads import list_findings, list_runs
from pharma_batch_record_checker.store.rereview import (
    Standing,
    rereview_all,
    rereview_batch,
)
from pharma_batch_record_checker.store.review_store import PostgresReviewStore

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is unset"
    ),
]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "approved_sample"
SAMPLE_BATCHES = ("B26-0412", "B26-0413", "P26-0201", "P26-0202")

# Versions relative to the engine's, so these tests hold for any RULE_VERSION.
LOADED = rules.RULE_VERSION
NEWER = f"{LOADED}.1"
OLDER = "0"


@pytest.fixture
def loaded(admin):
    """The approved sample, stored and reviewed once with the engine's rule version."""
    load_and_review(
        FIXTURES / "master_record.csv",
        FIXTURES / "batch_record.csv",
        PostgresReviewStore(),
    )


def _payloads(run_id: str) -> list[dict]:
    """A run's findings without their ids, to compare two runs."""
    return [
        {
            key: value
            for key, value in dataclasses.asdict(finding).items()
            if key not in {"finding_id", "run_id"}
        }
        for finding in list_findings(run_id)
    ]


def test_same_rules_change_nothing(loaded):
    """Every batch is CURRENT after load, so nothing is appended."""
    report = rereview_all()
    assert report.current == SAMPLE_BATCHES
    assert report.rereviewed == report.newer == report.unknown == report.failed == ()
    assert all(len(list_runs(batch_id)) == 1 for batch_id in SAMPLE_BATCHES)


def test_newer_rules_append_one_run_with_the_same_findings(loaded):
    """A forward re-review adds a run stamped with the running version.

    The rules haven't changed here, so its findings equal the first run's:
    the stored rows rebuild exactly what the CSVs gave.
    """
    (first,) = list_runs("B26-0413")
    result = rereview_batch("B26-0413", current=NEWER)
    assert result.standing is Standing.OLDER
    assert result.run_id is not None
    assert result.result_category == first.result_category == "Critical"
    runs = list_runs("B26-0413")
    assert [run.rule_version for run in runs] == [LOADED, NEWER]
    assert runs[-1].run_id == result.run_id
    assert _payloads(result.run_id) == _payloads(first.run_id)
    assert verify_chain() is None


def test_rereview_twice_is_a_no_op(loaded):
    """The second call finds the batch CURRENT and stores nothing."""
    rereview_batch("B26-0413", current=NEWER)
    again = rereview_batch("B26-0413", current=NEWER)
    assert again.standing is Standing.CURRENT
    assert again.run_id is None
    assert len(list_runs("B26-0413")) == 2


def test_older_rules_never_review_over_newer_ones(loaded):
    """Running rules older than the latest run: refused, nothing written."""
    result = rereview_batch("B26-0413", current=OLDER)
    assert result.standing is Standing.NEWER
    assert result.run_id is None
    assert len(list_runs("B26-0413")) == 1


def test_unknown_version_is_refused(loaded):
    """An unorderable running version never writes a run."""
    result = rereview_batch("B26-0413", current="next")
    assert result.standing is Standing.UNKNOWN
    assert len(list_runs("B26-0413")) == 1


def test_batch_without_a_run_is_reviewed(loaded):
    """A stored batch with no run (an interrupted load) gets its first run."""
    (step,) = [
        BatchStep.from_row(
            BatchRow(
                batch_id="B26-0999",
                master_record_id="MBR-SYN500",
                master_version="3",
                step_id="S-DISP-01",
                reading=None,
                unit=None,
                performed_by="OP-901",
                performed_at="2026-10-12T07:00",
                verified_by=None,
                verified_at=None,
                comment=None,
            )
        )
    ]
    insert_batch([step])
    result = rereview_batch("B26-0999")
    assert result.standing is Standing.NOT_REVIEWED
    (run,) = list_runs("B26-0999")
    assert run.run_id == result.run_id
    assert run.result_category == result.result_category


def test_rereview_all_moves_every_batch_forward_once(loaded):
    """All four batches get one new run; a second sync finds them current."""
    report = rereview_all(current=NEWER)
    assert report.rereviewed == SAMPLE_BATCHES
    assert all(len(list_runs(batch_id)) == 2 for batch_id in SAMPLE_BATCHES)
    again = rereview_all(current=NEWER)
    assert again.current == SAMPLE_BATCHES
    assert again.rereviewed == ()
    assert verify_chain() is None


def test_rereview_all_with_older_rules_reports_newer(loaded):
    """Older running rules leave every batch alone and say so."""
    report = rereview_all(current=OLDER)
    assert report.newer == SAMPLE_BATCHES
    assert report.rereviewed == ()
