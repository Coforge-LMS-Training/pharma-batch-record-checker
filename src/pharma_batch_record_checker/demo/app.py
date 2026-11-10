"""Flask pages for a stored review.

GET / lists batches, or the runs stored on a UTC day. GET /batches/<id>
shows that batch's runs and the latest run's findings. POST a note calls
insert_finding_note and redirects back. The CSV route returns
export_batch_csv. There is no login and no release action.
"""

import uuid
from datetime import date
from functools import wraps
from typing import Any

import psycopg
from flask import Flask, Response, redirect, render_template, request, url_for

from pharma_batch_record_checker.demo.view import (
    NEEDS_A_LOOK,
    format_decimal,
    format_store_time,
    group_findings,
)
from pharma_batch_record_checker.store.connection import connect
from pharma_batch_record_checker.store.errors import (
    BatchNotFound,
    FindingNotFound,
    MasterNotFound,
)
from pharma_batch_record_checker.store.notes import insert_finding_note
from pharma_batch_record_checker.store.reads import (
    export_batch_csv,
    get_batch,
    get_master,
    list_batches,
    list_findings,
    list_notes,
    list_runs,
    list_runs_on,
)


def create_app() -> Flask:
    """The review demo. It connects only when a page asks the store."""
    app = Flask(__name__)

    @app.after_request
    def _nosniff(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/")
    @stored
    def index() -> str | tuple[str, int]:
        needs_look = request.args.get("needs_look") == "1"
        raw_day = request.args.get("on", "").strip()
        day, day_error = _parse_day(raw_day)
        if day is not None:
            runs = list_runs_on(day)
            if needs_look:
                runs = tuple(run for run in runs if run.result_category in NEEDS_A_LOOK)
            return render_template(
                "index.html",
                mode="runs",
                runs=runs,
                batches=(),
                needs_look=needs_look,
                on=raw_day,
                day_error=day_error,
                format_store_time=format_store_time,
            )
        batches = list_batches()
        if needs_look:
            batches = tuple(
                batch for batch in batches if batch.result_category in NEEDS_A_LOOK
            )
        return render_template(
            "index.html",
            mode="batches",
            runs=(),
            batches=batches,
            products=_product_names(batches),
            needs_look=needs_look,
            on=raw_day,
            day_error=day_error,
            format_store_time=format_store_time,
        )

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
    body = render_template(
        "batch.html",
        batch=batch,
        master=master,
        runs=runs,
        latest=latest,
        groups=groups,
        batch_size="" if master is None else format_decimal(master.batch_size),
        note_error=note_error,
        note_finding_id=note_finding_id,
        draft_name=draft_name,
        draft_text=draft_text,
        format_store_time=format_store_time,
    )
    return body
