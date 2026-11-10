"""The review demo's CSV route and note form (PHA-53, PHA-56).

Synthetic approved sample only. These tests skip when DATABASE_URL is unset.
"""

import os
from pathlib import Path

import pytest

from pharma_batch_record_checker.demo.app import create_app
from pharma_batch_record_checker.store.load import load_and_review
from pharma_batch_record_checker.store.reads import (
    export_batch_csv,
    list_findings,
    list_notes,
    list_runs,
)
from pharma_batch_record_checker.store.review_store import PostgresReviewStore

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


def test_export_route_returns_the_batch_csv(admin):
    """GET /batches/B26-0413/export.csv is the stored history, unchanged."""
    load_and_review(MASTER_CSV, BATCH_CSV, PostgresReviewStore())
    response = create_app().test_client().get("/batches/B26-0413/export.csv")
    assert response.status_code == 200
    assert response.content_type.startswith("text/csv")
    assert response.headers["Content-Disposition"] == (
        'attachment; filename="B26-0413-history.csv"'
    )
    assert response.get_data(as_text=True) == export_batch_csv("B26-0413")


def test_note_form_returns_to_the_batch_without_changing_the_finding(admin):
    """A note is appended, the reading stays, and the page has no release control."""
    load_and_review(MASTER_CSV, BATCH_CSV, PostgresReviewStore())
    (run,) = list_runs("B26-0413")
    findings = list_findings(run.run_id)
    coat = next(
        item
        for item in findings
        if item.step_id == "S-COAT-02" and item.rule_id == "out_of_range"
    )
    client = create_app().test_client()
    posted = client.post(
        f"/findings/{coat.finding_id}/notes",
        data={
            "reviewer_name": "Reviewer A",
            "note_text": "Left the reading as written.",
        },
    )
    assert posted.status_code == 303
    assert posted.headers["Location"].endswith("/batches/B26-0413")

    page = client.get("/batches/B26-0413")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Left the reading as written." in html
    assert "Reviewer A" in html
    assert "S-COAT-02" in html
    assert "out_of_range" in html
    assert "same_person" in html
    assert "Add note" in html
    assert ">Approve<" not in html
    assert ">Release<" not in html
    assert ">Reject<" not in html
    assert list_notes(coat.finding_id)[0].note_text == "Left the reading as written."
    stored = next(
        item for item in list_findings(run.run_id) if item.finding_id == coat.finding_id
    )
    assert stored.reading == coat.reading

    blank = client.post(
        f"/findings/{coat.finding_id}/notes",
        data={"reviewer_name": "   ", "note_text": "Not stored."},
    )
    assert blank.status_code == 400
    assert len(list_notes(coat.finding_id)) == 1
