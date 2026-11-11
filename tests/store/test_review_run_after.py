"""Forward-only run append: insert_review_run_after and latest_runs.

A re-review appends a run only if the batch's latest run is still the one
the caller looked at, so a double click or a parallel request cannot add two
runs. Synthetic approved-sample rows only.
"""

import os
import threading
from pathlib import Path

import pytest

from pharma_batch_record_checker.handoff import build_payload
from pharma_batch_record_checker.store.errors import BatchNotFound, StaleReview
from pharma_batch_record_checker.store.hashchain import verify_chain
from pharma_batch_record_checker.store.load import load_and_review
from pharma_batch_record_checker.store.reads import latest_runs, list_runs
from pharma_batch_record_checker.store.review_store import PostgresReviewStore

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is unset"
    ),
]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "approved_sample"
MASTER_CSV = FIXTURES / "master_record.csv"
BATCH_CSV = FIXTURES / "batch_record.csv"
BATCH = "B26-0413"


@pytest.fixture
def loaded(admin):
    """The approved sample stored and reviewed once; returns the review run."""
    return load_and_review(MASTER_CSV, BATCH_CSV, PostgresReviewStore())


def _append(run, expected: str | None, rule_version: str = "1.0.1") -> str:
    """Append B26-0413's findings again as a run after expected."""
    batch = run.batches[BATCH]
    return PostgresReviewStore().insert_review_run_after(
        BATCH,
        rule_version,
        batch.result_category,
        build_payload(batch),
        expected_latest_run_id=expected,
    )


def test_latest_runs_names_every_batch_and_its_latest_run(loaded):
    """One entry per stored batch, each with the run the pages show."""
    latest = latest_runs()
    assert set(latest) == set(loaded.batches)
    for batch_id, run in latest.items():
        assert run is not None
        assert run == list_runs(batch_id)[-1]


def test_append_after_the_latest_run_adds_one_run(loaded):
    """With the right expected run, the new run is stored and becomes latest."""
    (first,) = list_runs(BATCH)
    run_id = _append(loaded, first.run_id)
    runs = list_runs(BATCH)
    assert [run.run_id for run in runs] == [first.run_id, run_id]
    assert runs[-1].rule_version == "1.0.1"
    assert latest_runs()[BATCH] == runs[-1]
    assert verify_chain() is None


def test_append_after_a_stale_run_writes_nothing(loaded):
    """When another run came first, the call raises and stores nothing."""
    (first,) = list_runs(BATCH)
    _append(loaded, first.run_id)
    with pytest.raises(StaleReview):
        _append(loaded, first.run_id, "1.0.2")
    assert [run.rule_version for run in list_runs(BATCH)] == ["1.0.0", "1.0.1"]


def test_expecting_no_run_fails_when_one_exists(loaded):
    """expected_latest_run_id=None means "not reviewed yet"; a run exists here."""
    with pytest.raises(StaleReview):
        _append(loaded, None)
    assert len(list_runs(BATCH)) == 1


def test_append_to_an_unknown_batch_is_refused(loaded):
    """No batch, no run."""
    with pytest.raises(BatchNotFound):
        PostgresReviewStore().insert_review_run_after(
            "B26-9999", "1.0.1", "No Findings", (), expected_latest_run_id=None
        )


def test_parallel_appends_store_exactly_one_run(loaded):
    """Two requests after the same run: one wins, the other is stale."""
    (first,) = list_runs(BATCH)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def attempt() -> None:
        """Wait for the other thread, then try to append."""
        barrier.wait()
        try:
            _append(loaded, first.run_id)
            outcomes.append("stored")
        except StaleReview:
            outcomes.append("stale")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["stale", "stored"]
    assert len(list_runs(BATCH)) == 2
    assert verify_chain() is None
