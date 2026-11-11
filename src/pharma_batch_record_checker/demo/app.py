"""Flask pages for a stored review.

GET / is the overview. GET /batches lists batches, filtered by status and
search text, or the runs stored on a UTC day. GET /batches/<id> shows that
batch's runs and the latest run's findings. GET /guide explains the results
and the checks. POST a note calls insert_finding_note and redirects back. The
CSV route returns export_batch_csv. There is no login and no release action.
"""

import os
import uuid
from dataclasses import replace
from datetime import date
from functools import wraps
from typing import Any

import psycopg
from flask import (
    Flask,
    Response,
    redirect,
    render_template,
    request,
    url_for,
)
from psycopg.rows import dict_row

from pharma_batch_record_checker.demo.view import (
    FAQ,
    GLOSSARY,
    NEEDS_A_LOOK,
    RESULT_GUIDE,
    ChainStatus,
    audit_entry,
    format_day,
    format_decimal,
    format_store_time,
    group_findings,
    guide_checks,
    overview,
    page_terms,
    process_strip,
    result_class,
    status_slug,
    step_results,
)
from pharma_batch_record_checker.store.connection import connect
from pharma_batch_record_checker.store.errors import (
    BatchNotFound,
    FindingNotFound,
    MasterNotFound,
)
from pharma_batch_record_checker.store.hashchain import master_entity_id, verify_chain
from pharma_batch_record_checker.store.notes import insert_finding_note
from pharma_batch_record_checker.store.reads import (
    EXPORT_COLUMNS,
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

# Footer branding. TODO(owner): confirm the team name and set CONTACT_EMAIL
# in .env; until then the placeholder below is shown.
BRAND = "Momentum Blue FDEs"
CONTACT_EMAIL_PLACEHOLDER = "contact@momentumblue.example"

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
        rule_guide=guide_checks(),
        glossary=GLOSSARY,
        faq=FAQ,
        page_terms=page_terms,
        format_store_time=format_store_time,
        format_day=format_day,
        brand=BRAND,
        current_year=date.today().year,
    )

    @app.context_processor
    def _site() -> dict[str, Any]:
        # Read per request so a changed .env needs only a restart, not a rebuild.
        return {
            "contact_email": os.environ.get("CONTACT_EMAIL", "").strip()
            or CONTACT_EMAIL_PLACEHOLDER,
        }

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

    @app.get("/reports/all-batches.csv")
    @stored
    def export_all_csv() -> Response:
        response = Response(all_batches_csv(), content_type="text/csv; charset=utf-8")
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{ALL_BATCHES_FILENAME}"'
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


# verify_chain re-checks every stored event, so it runs on each page view only
# while the chain is small; above this, the page offers a button instead.
AUTO_VERIFY_LIMIT = 2000


def _audit_trail(batch_id: str, master_id: str, master_version: str) -> list[Any]:
    """This batch's hash-chain events, oldest first, described for a reader.

    The batch's master import, the batch import, its review runs, and the
    notes on its findings. Read-only: audit_event is SELECT for the app role.
    """
    with connect() as conn:
        conn.row_factory = dict_row
        rows = conn.execute(
            """
            SELECT e.event_seq, e.event_type, e.entity_id, e.event_hash,
                   e.prev_event_hash, e.created_at,
                   r.rule_version, r.result_category,
                   (SELECT count(*) FROM finding rf WHERE rf.run_id = r.run_id)
                       AS run_findings,
                   n.reviewer_name, n.note_text, f.finding_id, f.step_id, f.rule_id,
                   (SELECT count(*) FROM batch_step bs WHERE bs.batch_id = %(batch)s)
                       AS batch_steps
            FROM audit_event e
            LEFT JOIN review_run r
                   ON e.event_type = 'review_run' AND r.run_id::text = e.entity_id
            LEFT JOIN finding_note n
                   ON e.event_type = 'finding_note' AND n.note_id::text = e.entity_id
            LEFT JOIN finding f ON f.finding_id = n.finding_id
            WHERE (e.event_type = 'master_import' AND e.entity_id = %(master)s)
               OR (e.event_type = 'batch_import' AND e.entity_id = %(batch)s)
               OR r.batch_id = %(batch)s
               OR f.batch_id = %(batch)s
            ORDER BY e.event_seq
            """,
            {"batch": batch_id, "master": master_entity_id(master_id, master_version)},
        ).fetchall()
    label = f"{master_id} v{master_version}"
    entries = [audit_entry({**row, "master_label": label}) for row in rows]
    runs = [i for i, entry in enumerate(entries) if entry.kind == "review_run"]
    if runs:
        entries[runs[-1]] = replace(entries[runs[-1]], latest=True)
    return entries


def _chain_status(force: bool) -> ChainStatus:
    """Run verify_chain when asked, or when the chain is small enough."""
    with connect() as conn:
        (events,) = conn.execute("SELECT count(*) FROM audit_event").fetchone()
    if not force and events > AUTO_VERIFY_LIMIT:
        return ChainStatus(checked=False, ok=False, events=events)
    failure = verify_chain()
    if failure is None:
        return ChainStatus(checked=True, ok=True, events=events)
    return ChainStatus(
        checked=True,
        ok=False,
        events=events,
        failed_seq=failure.event_seq,
        failed_check=failure.check,
    )


ALL_BATCHES_FILENAME = "all-batches-report.csv"


def all_batches_csv() -> str:
    """Every stored batch's auditor CSV in one file, under one header.

    Built from export_batch_csv, so its columns and rows are exactly the
    per-batch export's, batch after batch in batch_id order.
    """
    parts = [export_batch_csv(batch.batch_id) for batch in list_batches()]
    if not parts:
        return ",".join(EXPORT_COLUMNS) + "\r\n"
    header, *_ = parts[0].split("\r\n", 1)
    rows = [part.split("\r\n", 1)[1] for part in parts]
    return header + "\r\n" + "".join(rows)


def _batch_exists(batch_id: str) -> bool:
    try:
        get_batch(batch_id)
    except BatchNotFound:
        return False
    return True


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
    results: tuple[Any, ...] = groups
    strip: tuple[Any, ...] = ()
    if master is not None and latest is not None:
        results = step_results(master.steps, batch.steps, groups)
        strip = process_strip(master.steps, results)
    return render_template(
        "batch.html",
        nav="batches",
        batch=batch,
        master=master,
        total_steps=None if master is None else len(master.steps),
        runs=runs,
        latest=latest,
        groups=groups,
        results=results,
        summary=overview(groups),
        strip=strip,
        trail=_audit_trail(batch_id, batch.master_record_id, batch.master_version),
        chain=_chain_status(force=request.args.get("verify") == "1"),
        batch_size="" if master is None else format_decimal(master.batch_size),
        note_error=note_error,
        note_finding_id=note_finding_id,
        draft_name=draft_name,
        draft_text=draft_text,
    )
