"""Pages, navigation and plain-language help in the review demo.

Synthetic approved sample only. These tests skip when DATABASE_URL is unset.
"""

import os
import re
from pathlib import Path

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
    main = _main(html)
    assert "/email" not in main and "mailto:" not in main


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
    assert "Not required" in html  # no verifier needed on non-critical steps


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


def _audit(html: str) -> str:
    return html.split('id="audit"', 1)[1].split("</section>", 1)[0]


def _titles(html: str) -> list[str]:
    raw = re.findall(r'<p class="tl-title">(.*?)</p>', _audit(html), re.DOTALL)
    return [" ".join(re.sub(r"<[^>]+>", " ", t).split()) for t in raw]


def test_audit_trail_lists_this_batchs_events_in_order(client):
    trail = _titles(_html(client, "/batches/B26-0413"))
    assert trail == [
        "Master record stored",
        "Batch record stored",
        "Reviewed: Critical Latest review",
    ]
    html = _audit(_html(client, "/batches/B26-0413"))
    assert "MBR-SYN500 v3" in html
    assert "15 executed steps recorded by operators." in html
    assert "Rule version 1.0.0 found 10 findings." in html
    # Another batch's events stay off this page.
    assert "P26-0202" not in html


def test_audit_trail_verifies_the_chain(client):
    html = _audit(_html(client, "/batches/B26-0413"))
    assert "Chain verified" in html
    assert "all 10 recorded events still match" in html


def test_a_note_appears_in_the_audit_trail(client):
    (run,) = list_runs("B26-0413")
    finding = list_findings(run.run_id)[0]
    client.post(
        f"/findings/{finding.finding_id}/notes",
        data={"reviewer_name": "Reviewer C", "note_text": "Verifier asked."},
    )
    html = _html(client, "/batches/B26-0413")
    assert _titles(html)[-1] == "Note added by Reviewer C"
    assert f'href="#finding-{finding.finding_id}"' in _audit(html)
    assert "Verifier asked." in _audit(html)
    assert "Chain verified" in _audit(html)


def test_a_changed_row_shows_the_chain_as_broken(client, admin):
    # Only the admin role can bypass the append-only triggers; this simulates
    # someone editing the database behind the application's back.
    admin.execute(
        "UPDATE batch_step SET reading = 150.00 "
        "WHERE batch_id = 'B26-0413' AND step_id = 'S-DISP-01'"
    )
    admin.commit()
    html = _audit(_html(client, "/batches/B26-0413"))
    assert "Chain broken" in html
    assert "Stored history may have been altered." in html
    assert "tl-broken" in html


def test_large_chains_verify_only_on_request(client, monkeypatch):
    from pharma_batch_record_checker.demo import app as demo_app

    monkeypatch.setattr(demo_app, "AUTO_VERIFY_LIMIT", 0)
    html = _audit(_html(client, "/batches/B26-0413"))
    assert "Integrity not checked on this view." in html
    assert "Verify integrity" in html
    forced = _audit(_html(client, "/batches/B26-0413?verify=1"))
    assert "Chain verified" in forced


# ---------- Reports and footer ----------


def test_footer_has_copyright_and_contact(client, monkeypatch):
    monkeypatch.delenv("CONTACT_EMAIL", raising=False)
    html = _html(client, "/")
    footer = html.split('<footer class="footer">', 1)[1]
    assert "&copy; " in footer and "Momentum Blue FDEs" in footer
    assert 'href="mailto:contact@momentumblue.example?subject=' in footer
    assert "flags steps for a person to check" not in footer
    monkeypatch.setenv("CONTACT_EMAIL", "qa@momentumblue.example")
    assert "mailto:qa@momentumblue.example" in _html(client, "/")


def test_top_bar_has_all_batches_download(client):
    top = _html(client, "/batches").split("</header>", 1)[0]
    assert 'href="/reports/all-batches.csv" download' in top
    assert "mail" not in top.split('class="topbar-actions"', 1)[1]


def test_all_batches_report_joins_every_batch_export(client):
    from pharma_batch_record_checker.store.reads import export_batch_csv

    response = client.get("/reports/all-batches.csv")
    assert response.status_code == 200
    assert response.headers["Content-Disposition"] == (
        'attachment; filename="all-batches-report.csv"'
    )
    body = response.get_data(as_text=True)
    lines = body.split("\r\n")
    assert lines[0] == export_batch_csv("B26-0413").split("\r\n")[0]
    assert body.count("batch_id,run_id") == 1
    rows = [line for line in lines[1:] if line]
    per_batch = {
        b: len([x for x in export_batch_csv(b).split("\r\n")[1:] if x])
        for b in ("B26-0412", "B26-0413", "P26-0201", "P26-0202")
    }
    assert len(rows) == sum(per_batch.values())
    assert [r.split(",")[0] for r in rows] == sorted(r.split(",")[0] for r in rows)


def test_email_sending_is_gone(client):
    assert client.get("/email").status_code == 404
    assert client.post("/email", data={"to": "a@b.example"}).status_code in (404, 405)
    assert "email-dialog" not in _html(client, "/batches/B26-0413")


def test_batch_page_shows_every_step_with_passed_ones(client):
    html = _main(_html(client, "/batches/B26-0413"))
    assert '<h2 id="findings-title">Step-by-step results</h2>' in html
    assert html.count('data-status="passed"') == 7
    assert re.search(
        r'data-filter="passed">.*?Passed <span class="count">7</span>', html, re.DOTALL
    )
    assert re.search(
        r'data-filter="critical">.*?<span class="count">3</span>', html, re.DOTALL
    )
    assert re.search(r'data-filter="all">All <span class="count">16</span>', html)
    # A passed step shows what was recorded.
    gran01 = html.split('id="step-S-GRAN-01"', 1)[1].split("</article>", 1)[0]
    assert "Passed every check" in gran01
    assert "18.0 L" in gran01 and "18.0 to 20.0 L" in gran01
    # Every strip tile now links to its step card.
    assert len(re.findall(r'strip-clean">\s*<a href="#step-', html)) == 7


def test_clean_batch_lists_all_steps_as_passed(client):
    html = _main(_html(client, "/batches/B26-0412"))
    assert html.count('data-status="passed"') == 16
    assert 'data-filter="critical"' not in html
