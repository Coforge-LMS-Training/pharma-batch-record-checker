"""Messages and counts for re-review on the pages. No database.

The routes redirect with a short code or counts in the query string; these
helpers turn them into one notice, and ignore anything they don't know.
"""

from datetime import UTC, datetime

from pharma_batch_record_checker.demo.view import (
    rereview_notice,
    sync_notice,
    sync_state,
)
from pharma_batch_record_checker.store.reads import StoredRun


def _run(batch_id: str, rule_version: str) -> StoredRun:
    """A stored run with only the fields the counts read."""
    return StoredRun(
        run_id=f"run-{batch_id}",
        batch_id=batch_id,
        rule_version=rule_version,
        result_category="No Findings",
        created_at=datetime(2026, 11, 11, tzinfo=UTC),
    )


def test_done_notice_names_the_new_rules():
    """A stored re-review says which rules ran and that earlier runs stay."""
    notice = rereview_notice("done", "1.1.0", "1.1.0")
    assert notice is not None and notice.kind == "ok"
    assert "1.1.0" in notice.text
    assert "earlier" in notice.text


def test_newer_notice_explains_forward_only():
    """Older code on a newer review: nothing added, and why."""
    notice = rereview_notice("newer", "1.0.0", "1.1.0")
    assert notice is not None and notice.kind == "info"
    assert "1.1.0" in notice.text and "1.0.0" in notice.text
    assert "only move forward" in notice.text


def test_current_and_failed_notices():
    """Same rules: nothing added. Failure: an alert that nothing was added."""
    current = rereview_notice("current", "1.0.0", "1.0.0")
    assert current is not None and current.kind == "info"
    failed = rereview_notice("failed", "1.0.0", "1.0.0")
    assert failed is not None and failed.kind == "alert"
    assert "Nothing was added" in failed.text


def test_unknown_codes_show_nothing():
    """A code the page didn't send is ignored, not echoed."""
    assert rereview_notice("", "1.0.0", None) is None
    assert rereview_notice("<script>", "1.0.0", None) is None


def test_sync_notice_counts_what_happened():
    """The summary after "Re-review all" reads its counts from the query."""
    notice = sync_notice(
        {"synced": "3", "current": "1", "newer": "0", "unknown": "0", "failed": "0"},
        "1.1.0",
    )
    assert notice is not None and notice.kind == "ok"
    assert "3 batches re-reviewed with rules 1.1.0" in notice.text
    assert "1 already current" in notice.text


def test_sync_notice_flags_failures_and_ignores_junk():
    """Failures make it an alert; non-numbers mean no notice at all."""
    notice = sync_notice({"synced": "0", "failed": "2"}, "1.1.0")
    assert notice is not None and notice.kind == "alert"
    assert "2 failed" in notice.text
    assert sync_notice({}, "1.1.0") is None
    assert sync_notice({"synced": "x"}, "1.1.0") is None
    assert sync_notice({"synced": "-1"}, "1.1.0") is None


def test_sync_state_counts_batches_by_standing():
    """Older and unreviewed batches are due; newer and unknown are listed apart."""
    state = sync_state(
        {
            "A": _run("A", "1.0.0"),
            "B": _run("B", "1.1.0"),
            "C": None,
            "D": _run("D", "2.0.0"),
            "E": _run("E", "weird"),
        },
        "1.1.0",
    )
    assert state.due == 2
    assert state.newer == 1
    assert state.unknown == 1
    assert state.rules_version == "1.1.0"
