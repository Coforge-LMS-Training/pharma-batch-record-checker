"""Pages, navigation and plain-language help in the review demo.

Synthetic approved sample only. These tests skip when DATABASE_URL is unset.
"""

import os
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

from pharma_batch_record_checker.demo.app import create_app
from pharma_batch_record_checker.rules import RULE_IDS
from pharma_batch_record_checker.store.load import load_and_review
from pharma_batch_record_checker.store.reads import list_findings, list_runs
from pharma_batch_record_checker.store.review_store import PostgresReviewStore

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL is unset",
    ),
]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "approved_sample"


@pytest.fixture
def client(admin):
    load_and_review(
        FIXTURES / "master_record.csv",
        FIXTURES / "batch_record.csv",
        PostgresReviewStore(),
    )
    return create_app().test_client()


def _html(client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200, path
    return response.get_data(as_text=True)


def _main(html: str) -> str:
    """Only the page's own content: not the nav, help panel or footer."""
    return html.split('<main id="content"', 1)[1].split("</main>", 1)[0]


def _row(html: str, batch_id: str) -> str:
    match = re.search(
        rf"<tr[^>]*>\s*<td[^>]*>\s*<a[^>]*>{batch_id}</a>.*?</tr>", html, re.DOTALL
    )
    assert match, batch_id
    return match.group(0)


def test_every_page_has_the_main_navigation(client):
    for path, current in (
        ("/", "Overview"),
        ("/batches", "Batches"),
        ("/guide", "Guide"),
    ):
        html = _html(client, path)
        for label in ("Overview", "Batches", "Guide &amp; glossary"):
            assert label in html
        current_link = re.search(
            r'<a [^>]*aria-current="page"[^>]*>.*?</a>', html, re.DOTALL
        )
        assert current_link and current in current_link.group(0), path
    assert "Batch Record Review" in _html(client, "/batches/B26-0413")


def test_overview_counts_results_and_lists_what_needs_attention(client):
    html = _html(client, "/")
    assert re.search(r"Batches reviewed</span>\s*<span class=\"stat-value\">4<", html)
    assert re.search(r"Critical</span>\s*<span class=\"stat-value\">2<", html)
    attention = re.findall(r'class="attention-item" href="/batches/([^"]+)"', html)
    assert attention == ["B26-0413", "P26-0202"]


def test_batch_list_counts_each_batch(client):
    html = _html(client, "/batches")
    expected = {
        "B26-0412": (0, 0),
        "B26-0413": (9, 10),
        "P26-0201": (0, 0),
        "P26-0202": (4, 4),
    }
    for batch_id, (steps, findings) in expected.items():
        cells = re.findall(
            r'data-label="(?:Steps flagged|Findings)">(\d+)<', _row(html, batch_id)
        )
        assert cells == [str(steps), str(findings)], batch_id


def test_status_tabs_search_and_old_filter_link(client):
    critical = _main(_html(client, "/batches?status=critical"))
    assert "B26-0413" in critical and "B26-0412" not in critical
    assert 'href="/batches?status=critical" aria-current="true"' in critical
    assert "B26-0412" not in _main(_html(client, "/batches?needs_look=1"))
    search = _main(_html(client, "/batches?q=placeprol"))
    assert "P26-0201" in search and "B26-0413" not in search


def test_calendar_accepts_any_day_and_marks_review_days(client):
    (run,) = list_runs("B26-0413")
    today = run.created_at.date().isoformat()
    html = _html(client, "/batches")
    assert f"data-review-days='[\"{today}\"]'" in html
    assert 'type="date" name="on"' in html
    empty = _html(client, "/batches?on=2026-01-05")
    assert "No reviews were stored on this day." in empty
    assert "Mon 5 Jan 2026" in empty


def test_batch_page_header_is_the_app_not_the_batch(client):
    html = _html(client, "/batches/B26-0413")
    assert re.search(r'<nav class="crumbs".*?Batches.*?B26-0413', html, re.DOTALL)
    assert "Synthetic data · review only" not in html.split("<main")[0]


def test_export_and_email_are_icon_buttons(client):
    html = _html(client, "/batches/B26-0413")
    assert 'aria-label="Download review history as CSV"' in html
    assert 'href="/batches/B26-0413/export.csv" download' in html
    email = re.search(r'href="(mailto:[^"]+)"', html).group(1)
    assert "/batches/B26-0413/export.csv" in unquote(email)


def test_strip_and_findings_cover_every_flagged_step(client):
    html = _html(client, "/batches/B26-0413")
    (run,) = list_runs("B26-0413")
    findings = list_findings(run.run_id)
    assert html.count('class="strip-step ') == 16
    assert html.count('class="strip-step strip-clean"') == 7
    for finding in findings:
        assert f'id="finding-{finding.finding_id}"' in html
        assert f'data-rule="{finding.rule_id}"' in html


def test_rule_codes_are_never_shown_as_text(client):
    html = _html(client, "/batches/B26-0413")
    visible = re.sub(r"<[^>]+>", " ", html)
    for rule_id in RULE_IDS:
        assert rule_id not in visible, rule_id


def test_missing_values_are_called_out(client):
    html = _html(client, "/batches/B26-0413")
    assert "Verified by and Verified at were left empty" in html
    assert "no row at all" in html
    assert "Not recorded" in html


def test_glossary_help_on_pages_and_full_guide(client):
    batch = _html(client, "/batches/B26-0413")
    assert "New to batch records?" in batch
    assert "Critical step" in batch
    guide = _html(client, "/guide")
    for anchor in ('id="results"', 'id="checks"', 'id="glossary"', 'id="faq"'):
        assert anchor in guide
    assert "API (active pharmaceutical ingredient)" in guide
    assert "Can it approve, release or reject a batch?" in guide


def test_note_form_is_tucked_away_until_asked_for(client):
    html = _html(client, "/batches/B26-0413")
    assert '<details class="add-note">' in html
    assert '<details class="add-note" open>' not in html


def test_a_rejected_note_opens_its_form_with_the_message(client):
    (run,) = list_runs("B26-0413")
    finding = list_findings(run.run_id)[0]
    page = client.post(
        f"/findings/{finding.finding_id}/notes",
        data={"reviewer_name": "", "note_text": "Kept as typed."},
    )
    html = page.get_data(as_text=True)
    assert page.status_code == 400
    assert html.count('<details class="add-note" open>') == 1
    assert "Kept as typed." in html


def test_a_saved_note_shows_with_its_count(client):
    (run,) = list_runs("B26-0413")
    finding = list_findings(run.run_id)[0]
    page = client.post(
        f"/findings/{finding.finding_id}/notes",
        data={"reviewer_name": "Reviewer B", "note_text": "Checked."},
    )
    assert page.status_code == 303
    html = _html(client, "/batches/B26-0413")
    assert "Reviewer B ·" in html
    assert re.search(r'Notes <span class="count">1</span>', html)


def test_clean_batch_says_so_in_plain_words(client):
    html = _html(client, "/batches/B26-0412")
    assert "Every step matches its master record." in html
    assert 'class="strip-step strip-clean"' in html


def test_unknown_batch_is_a_friendly_404(client):
    response = client.get("/batches/NOPE-0001")
    assert response.status_code == 404
    assert "No batch NOPE-0001 is stored." in response.get_data(as_text=True)
