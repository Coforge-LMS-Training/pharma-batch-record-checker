"""Re-review from the pages: one batch, or every batch, forward only.

The approved sample is loaded with the engine's RULE_VERSION. Newer or older running
rules are simulated by patching rules.RULE_VERSION, which the re-review reads
at call time. Synthetic rows only; skipped when DATABASE_URL is unset.
"""

import os
from pathlib import Path

import pytest

from pharma_batch_record_checker import rules
from pharma_batch_record_checker.demo.app import create_app
from pharma_batch_record_checker.demo.view import RULE_GUIDE
from pharma_batch_record_checker.store.hashchain import verify_chain
from pharma_batch_record_checker.store.load import load_and_review
from pharma_batch_record_checker.store.reads import list_runs
from pharma_batch_record_checker.store.review_store import PostgresReviewStore

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is unset"
    ),
]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "approved_sample"
BATCH = "B26-0413"
SAMPLE_BATCHES = ("B26-0412", "B26-0413", "P26-0201", "P26-0202")

# Versions relative to the engine's, so these tests hold for any RULE_VERSION.
LOADED = rules.RULE_VERSION
NEWER = f"{LOADED}.1"
OLDER = "0"


@pytest.fixture
def client(admin):
    """The approved sample stored and reviewed once, and a test client."""
    load_and_review(
        FIXTURES / "master_record.csv",
        FIXTURES / "batch_record.csv",
        PostgresReviewStore(),
    )
    return create_app().test_client()


@pytest.fixture
def newer_rules(monkeypatch):
    """Run as if the engine had rules newer than the stored runs."""
    monkeypatch.setattr(rules, "RULE_VERSION", NEWER)


@pytest.fixture
def older_rules(monkeypatch):
    """Run as if older code were serving this database."""
    monkeypatch.setattr(rules, "RULE_VERSION", OLDER)


def _main(client, path: str) -> str:
    """The page's own content, asserting a 200."""
    response = client.get(path)
    assert response.status_code == 200, path
    html = response.get_data(as_text=True)
    return html.split('<main id="content"', 1)[1].split("</main>", 1)[0]


def _rereview_form(html: str) -> bool:
    """True when the page offers this batch's re-review button."""
    return f'action="/batches/{BATCH}/rereview"' in html


def test_same_rules_offer_no_button_and_show_the_version(client):
    """After load every batch is current: no button, and the rules are shown."""
    html = _main(client, f"/batches/{BATCH}")
    assert not _rereview_form(html)
    assert f"<dt>Rules</dt><dd>{LOADED}" in html
    overview = _main(client, "/")
    assert 'action="/rereview-all"' not in overview
    assert f"Rules {LOADED}" in overview


def test_newer_rules_offer_the_button(client, newer_rules):
    """A batch reviewed with older rules can be re-reviewed."""
    html = _main(client, f"/batches/{BATCH}")
    assert _rereview_form(html)
    assert f"Re-review with rules {NEWER}" in html


def test_rereview_appends_a_run_and_redirects(client, newer_rules):
    """POST stores one run, then the page shows it as the latest review."""
    response = client.post(f"/batches/{BATCH}/rereview")
    assert response.status_code == 303
    assert response.headers["Location"].endswith(f"/batches/{BATCH}?rereview=done")
    assert [run.rule_version for run in list_runs(BATCH)] == [LOADED, NEWER]
    html = _main(client, f"/batches/{BATCH}?rereview=done")
    assert f"Re-reviewed with rules {NEWER}" in html
    assert html.count("Reviewed: Critical") == 2
    assert not _rereview_form(html)
    assert verify_chain() is None


def test_second_post_adds_nothing(client, newer_rules):
    """A repeated POST (a double click) finds the batch current."""
    client.post(f"/batches/{BATCH}/rereview")
    response = client.post(f"/batches/{BATCH}/rereview")
    assert response.headers["Location"].endswith("?rereview=current")
    assert len(list_runs(BATCH)) == 2


def test_older_code_never_reviews_over_newer_rules(client, older_rules):
    """Older running rules: a note instead of a button, and POST stores nothing."""
    html = _main(client, f"/batches/{BATCH}")
    assert not _rereview_form(html)
    assert f"newer rules ({LOADED}) than this app ({OLDER})" in html
    response = client.post(f"/batches/{BATCH}/rereview")
    assert response.headers["Location"].endswith("?rereview=newer")
    assert len(list_runs(BATCH)) == 1


def test_unknown_batch_is_404(client, newer_rules):
    """No stored batch, no run."""
    assert client.post("/batches/B26-9999/rereview").status_code == 404


def test_rereview_is_post_only(client, newer_rules):
    """A link or a crawler can't trigger a review."""
    assert client.get(f"/batches/{BATCH}/rereview").status_code == 405
    assert client.get("/rereview-all").status_code == 405
    assert len(list_runs(BATCH)) == 1


def test_cross_origin_posts_are_refused(client, newer_rules):
    """A form on another site can't make this app store a run."""
    for headers in (
        {"Origin": "http://evil.example"},
        {"Referer": "http://evil.example/page"},
        {"Origin": "null"},
    ):
        assert (
            client.post(f"/batches/{BATCH}/rereview", headers=headers).status_code
            == 403
        )
        assert client.post("/rereview-all", headers=headers).status_code == 403
    assert len(list_runs(BATCH)) == 1


def test_same_origin_post_is_accepted(client, newer_rules):
    """The app's own form sends its own origin."""
    response = client.post(
        f"/batches/{BATCH}/rereview", headers={"Origin": "http://localhost"}
    )
    assert response.status_code == 303


def test_rereview_all_moves_every_batch_forward(client, newer_rules):
    """The Overview button re-reviews all four, then disappears."""
    overview = _main(client, "/")
    assert 'action="/rereview-all"' in overview
    assert "Re-review all (4)" in overview
    response = client.post("/rereview-all")
    assert response.status_code == 303
    location = response.headers["Location"]
    assert "synced=4" in location and "failed=0" in location
    assert all(len(list_runs(batch_id)) == 2 for batch_id in SAMPLE_BATCHES)
    after = _main(client, "/?" + location.split("?", 1)[1])
    assert f"4 batches re-reviewed with rules {NEWER}" in after
    assert 'action="/rereview-all"' not in after


def test_overview_notes_batches_on_newer_rules(client, older_rules):
    """Older code: no button, and the batches it won't touch are counted."""
    overview = _main(client, "/")
    assert 'action="/rereview-all"' not in overview
    assert "4 batches were reviewed with newer rules" in overview


def test_pages_still_offer_no_release_action(client, newer_rules):
    """Re-review adds a run; there is still no approve, release or reject."""
    for path in ("/", f"/batches/{BATCH}"):
        html = _main(client, path)
        for word in (">Approve<", ">Release<", ">Reject<"):
            assert word not in html


def test_guide_lists_only_the_engine_checks(client):
    """The Guide shows the checks this engine runs, not every stored rule id."""
    guide = _main(client, "/guide")
    checks = guide.split('class="check-grid"', 1)[1].split("</ul>", 1)[0]
    assert checks.count("<li>") == len(rules.RULE_IDS)
    for rule_id, entry in RULE_GUIDE.items():
        assert (entry.title in checks) == (rule_id in rules.RULE_IDS), rule_id
