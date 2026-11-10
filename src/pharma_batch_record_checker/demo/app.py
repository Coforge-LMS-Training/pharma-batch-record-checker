"""Flask pages for a stored review.

GET / is the overview. GET /batches lists batches, filtered by status and
search text, or the runs stored on a UTC day. GET /batches/<id> shows that
batch's runs and the latest run's findings. GET /guide explains the results
and the checks. POST a note calls insert_finding_note and redirects back. The
CSV route returns export_batch_csv. There is no login and no release action.
"""

import uuid
from datetime import date
from functools import wraps
from typing import Any
from urllib.parse import quote

import psycopg
from flask import Flask, Response, redirect, render_template, request, url_for

from pharma_batch_record_checker.demo.view import (
    FAQ,
    GLOSSARY,
    NEEDS_A_LOOK,
    RESULT_GUIDE,
    RULE_GUIDE,
    format_day,
    format_decimal,
    format_store_time,
    group_findings,
    overview,
    page_terms,
    process_strip,
    result_class,
    status_slug,
)
from pharma_batch_record_checker.store.connection import connect
from pharma_batch_record_checker.store.errors import (
    BatchNotFound,
    FindingNotFound,
    MasterNotFound,
)
from pharma_batch_record_checker.store.notes import insert_finding_note
from pharma_batch_record_checker.store.reads import (
    BatchSummary,
    export_batch_csv,
    get_batch,
    get_master,
    list_batches,
    list_findings,
    list_notes,
    list_runs,
    list_runs_on,
)

# ?status= values on the batch list. "attention" is Critical or Needs Review.
STATUS_FILTERS: dict[str, frozenset[str]] = {
    "critical": frozenset({"Critical"}),
    "needs-review": frozenset({"Needs Review"}),
    "attention": NEEDS_A_LOOK,
    "no-findings": frozenset({"No Findings"}),
}


def create_app() -> Flask:
    """The review demo. It connects only when a page asks the store."""
    app = Flask(__name__)
    app.jinja_env.globals.update(
        result_class=result_class,
        status_slug=status_slug,
        result_guide=RESULT_GUIDE,
        rule_guide=RULE_GUIDE,
        glossary=GLOSSARY,
        faq=FAQ,
        page_terms=page_terms,
        format_store_time=format_store_time,
        format_day=format_day,
    )

    @app.after_request
    def _nosniff(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/")
    @stored
    def overview_page() -> str:
        rows = _batch_rows(list_batches())
        attention = [row for row in rows if row["result"] in NEEDS_A_LOOK]
        attention.sort(key=lambda row: (row["result"] != "Critical", row["batch_id"]))
        return render_template(
            "overview.html",
            nav="overview",
            stats=_stats(rows),
            attention=attention,
            total=len(rows),
        )

    @app.get("/guide")
    def guide_page() -> str:
        return render_template("guide.html", nav="guide")

    @app.get("/batches")
    @stored
    def index() -> str | tuple[str, int]:
        status = request.args.get("status", "")
        if request.args.get("needs_look") == "1":
            status = "attention"
        if status not in STATUS_FILTERS:
            status = ""
        query = request.args.get("q", "").strip()
        raw_day = request.args.get("on", "").strip()
        day, day_error = _parse_day(raw_day)
        all_rows = _batch_rows(list_batches())
        common = {
            "nav": "batches",
            "status": status,
            "query": query,
            "on": raw_day,
            "day": day,
            "day_error": day_error,
            "review_days": _review_days(),
            "stats": _stats(all_rows),
        }
        if day is not None:
            runs = list_runs_on(day)
            if status:
                runs = tuple(
                    r for r in runs if r.result_category in STATUS_FILTERS[status]
                )
            if query:
                runs = tuple(r for r in runs if query.lower() in r.batch_id.lower())
            return render_template("batches.html", mode="runs", runs=runs, **common)
        rows = all_rows
        if status:
            rows = [row for row in rows if row["result"] in STATUS_FILTERS[status]]
        if query:
            needle = query.lower()
            rows = [row for row in rows if needle in row["search"]]
        return render_template("batches.html", mode="batches", rows=rows, **common)

    @app.get("/batches/<batch_id>")
    @stored
    def batch_page(batch_id: str) -> str | tuple[str, int]:
        return _render_batch(batch_id)

    @app.post("/findings/<finding_id>/notes")
    @stored
    def add_note(finding_id: str) -> str | tuple[str, int] | Response:
        batch_id = _batch_id_for_finding(finding_id)
        if batch_id is None:
            return render_template("missing.html", batch_id=None), 404
        reviewer_name = request.form.get("reviewer_name", "")
        note_text = request.form.get("note_text", "")
        if not isinstance(reviewer_name, str) or not isinstance(note_text, str):
            reviewer_name = ""
            note_text = ""
        if reviewer_name.strip() == "" or note_text.strip() == "":
            return _render_batch(
                batch_id,
                note_error="Enter a name and a note. Both are stored as written.",
                note_finding_id=finding_id,
                draft_name=reviewer_name,
                draft_text=note_text,
            ), 400
        try:
            insert_finding_note(finding_id, reviewer_name, note_text)
        except FindingNotFound:
            return render_template("missing.html", batch_id=None), 404
        except ValueError:
            return _render_batch(
                batch_id,
                note_error="Enter a name and a note. Both are stored as written.",
                note_finding_id=finding_id,
                draft_name=reviewer_name,
                draft_text=note_text,
            ), 400
        return redirect(url_for("batch_page", batch_id=batch_id), code=303)

    @app.get("/batches/<batch_id>/export.csv")
    @stored
    def export_csv(batch_id: str) -> Response | tuple[str, int]:
        try:
            get_batch(batch_id)
            body = export_batch_csv(batch_id)
        except BatchNotFound:
            return render_template("missing.html", batch_id=batch_id), 404
        response = Response(body, content_type="text/csv; charset=utf-8")
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{batch_id}-history.csv"'
        )
        return response

    return app


def stored(view: Any) -> Any:
    """Render a store outage instead of a traceback."""

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return view(*args, **kwargs)
        except psycopg.OperationalError:
            return render_template("unavailable.html", missing_url=False), 503
        except RuntimeError as exc:
            if "DATABASE_URL" not in str(exc):
                raise
            return render_template("unavailable.html", missing_url=True), 503

    return wrapped


def _parse_day(raw_day: str) -> tuple[date | None, str | None]:
    if raw_day == "":
        return None, None
    try:
        return date.fromisoformat(raw_day), None
    except ValueError:
        return None, "Use a date as YYYY-MM-DD. That day is read in UTC."


def _batch_rows(batches: tuple[BatchSummary, ...]) -> list[dict[str, Any]]:
    """One display row per batch: ids, product, latest result, and counts."""
    products = _product_names(batches)
    counts = _latest_counts()
    rows = []
    for batch in batches:
        product = products[(batch.master_record_id, batch.master_version)]
        steps, findings = counts.get(batch.batch_id, (None, None))
        master = f"{batch.master_record_id} v{batch.master_version}"
        rows.append(
            {
                "batch_id": batch.batch_id,
                "master": master,
                "product": product,
                "result": batch.result_category,
                "status": status_slug(batch.result_category),
                "reviewed": batch.created_at,
                "steps": steps,
                "findings": findings,
                "search": f"{batch.batch_id} {master} {product}".lower(),
            }
        )
    return rows


def _stats(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(rows),
        "critical": sum(row["result"] == "Critical" for row in rows),
        "needs-review": sum(row["result"] == "Needs Review" for row in rows),
        "no-findings": sum(row["result"] == "No Findings" for row in rows),
        "findings": sum(row["findings"] or 0 for row in rows),
    }


def _product_names(batches: Any) -> dict[tuple[str, str], str]:
    names: dict[tuple[str, str], str] = {}
    for batch in batches:
        key = (batch.master_record_id, batch.master_version)
        if key in names:
            continue
        try:
            names[key] = get_master(*key).product_name
        except MasterNotFound:
            names[key] = ""
    return names


def _latest_counts() -> dict[str, tuple[int, int]]:
    """(flagged steps, findings) in each batch's latest run, in one query.

    Latest is the same run list_batches reports: greatest created_at, then
    greatest run_id. A batch with no run is absent.
    """
    with connect() as conn:
        rows = conn.execute(
            """
            WITH latest AS (
                SELECT DISTINCT ON (batch_id) batch_id, run_id
                FROM review_run
                ORDER BY batch_id, created_at DESC, run_id DESC
            )
            SELECT latest.batch_id,
                   count(DISTINCT finding.step_id) AS steps,
                   count(finding.finding_id) AS findings
            FROM latest
            LEFT JOIN finding ON finding.run_id = latest.run_id
            GROUP BY latest.batch_id
            """
        ).fetchall()
    return {batch_id: (steps, findings) for batch_id, steps, findings in rows}


def _review_days() -> list[str]:
    """Every UTC day with a stored run, as YYYY-MM-DD, for the calendar dots."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT (created_at AT TIME ZONE 'UTC')::date AS day
            FROM review_run
            ORDER BY day DESC
            """
        ).fetchall()
    return [row[0].isoformat() for row in rows]


def _batch_id_for_finding(finding_id: str) -> str | None:
    try:
        uuid.UUID(finding_id)
    except ValueError:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT batch_id FROM finding WHERE finding_id = %s",
            (finding_id,),
        ).fetchone()
    if row is None:
        return None
    return row[0]


def _email_link(batch_id: str) -> str:
    """A mailto: link carrying the CSV's address. Mail links cannot attach files."""
    csv_url = url_for("export_csv", batch_id=batch_id, _external=True)
    page_url = url_for("batch_page", batch_id=batch_id, _external=True)
    subject = f"Batch {batch_id} review history (synthetic data)"
    body = (
        f"Review history for batch {batch_id}.\n\n"
        f"Download the CSV: {csv_url}\n"
        f"Open the review: {page_url}\n"
    )
    return f"mailto:?subject={quote(subject)}&body={quote(body)}"


def _render_batch(
    batch_id: str,
    *,
    note_error: str | None = None,
    note_finding_id: str | None = None,
    draft_name: str = "",
    draft_text: str = "",
) -> str | tuple[str, int]:
    try:
        batch = get_batch(batch_id)
    except BatchNotFound:
        return render_template("missing.html", batch_id=batch_id), 404
    try:
        master = get_master(batch.master_record_id, batch.master_version)
    except MasterNotFound:
        master = None
    runs = list_runs(batch_id)
    latest = runs[-1] if runs else None
    groups: tuple[Any, ...] = ()
    if latest is not None:
        findings = list_findings(latest.run_id)
        notes = {
            finding.finding_id: list_notes(finding.finding_id) for finding in findings
        }
        groups = group_findings(findings, notes)
    strip = (
        () if master is None or latest is None else process_strip(master.steps, groups)
    )
    return render_template(
        "batch.html",
        nav="batches",
        batch=batch,
        master=master,
        total_steps=None if master is None else len(master.steps),
        runs=runs,
        latest=latest,
        groups=groups,
        summary=overview(groups),
        strip=strip,
        email_link=_email_link(batch_id),
        batch_size="" if master is None else format_decimal(master.batch_size),
        note_error=note_error,
        note_finding_id=note_finding_id,
        draft_name=draft_name,
        draft_text=draft_text,
    )
