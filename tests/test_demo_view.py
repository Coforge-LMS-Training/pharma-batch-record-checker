"""Plain-language view values for the review demo. No database."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from pharma_batch_record_checker.demo.view import (
    FAQ,
    GLOSSARY,
    MISSING,
    NOT_RECORDED,
    PAGE_TERMS,
    RESULT_GUIDE,
    RULE_GUIDE,
    group_findings,
    overview,
    page_terms,
    process_strip,
    range_view,
    result_class,
    rule_guide,
    status_slug,
)
from pharma_batch_record_checker.rules import RULE_IDS
from pharma_batch_record_checker.store.reads import (
    StoredFinding,
    StoredMasterStep,
    StoredNote,
)

BASE = StoredFinding(
    finding_id="f-1",
    run_id="r-1",
    finding_seq=0,
    rule_id="out_of_range",
    step_severity="Critical",
    detail="Coating weight gain 3.6 % outside 2.5 to 3.5 (inclusive)",
    batch_id="B26-9999",
    master_record_id="MBR-SYN500",
    master_version="3",
    step_id="S-COAT-02",
    step_seq=14,
    unit_procedure="Coating",
    operation="Film coating",
    phase="Check coat",
    parameter="Coating weight gain",
    critical=True,
    expected_unit="%",
    min_value=Decimal("2.5"),
    max_value=Decimal("3.5"),
    reading=Decimal("3.6"),
    recorded_unit="%",
    performed_by="OP-900",
    performed_at=datetime(2026, 10, 13, 13, 30),
    verified_by="OP-900",
    verified_at=datetime(2026, 10, 13, 13, 40),
    comment=None,
)


def _finding(**changes: object) -> StoredFinding:
    return replace(BASE, **changes)


def _only(findings: list[StoredFinding]):
    (procedure,) = group_findings(findings, {})
    return procedure.steps[0].findings[0]


def test_every_rule_has_a_plain_title_and_meaning():
    assert set(RULE_GUIDE) == set(RULE_IDS)
    for rule_id, guide in RULE_GUIDE.items():
        assert guide.title and guide.meaning, rule_id
        assert guide.title != rule_id


def test_unknown_rule_falls_back_to_its_code():
    guide = rule_guide("not_a_rule")
    assert guide.title == "not_a_rule"
    assert guide.fields == frozenset()


def test_result_guide_uses_the_three_agreed_words():
    assert [word for word, _ in RESULT_GUIDE] == [
        "Critical",
        "Needs Review",
        "No Findings",
    ]


def test_result_class_and_status_slug():
    assert result_class("Critical") == "badge badge-critical"
    assert result_class("Needs Review") == "badge badge-needs-review"
    assert result_class("No Findings") == "badge badge-no-findings"
    assert status_slug(None) == "none"


def test_two_findings_on_one_step_stay_two_and_share_one_step():
    coat_same = _finding(finding_id="f-2", finding_seq=1, rule_id="same_person")
    (procedure,) = group_findings([_finding(), coat_same], {})
    (step,) = procedure.steps
    assert [f.rule_id for f in step.findings] == ["out_of_range", "same_person"]
    assert (step.operation, step.phase) == ("Film coating", "Check coat")
    assert step.status == "critical"


def test_snapshot_flags_only_the_fields_the_rule_is_about():
    finding = _only([_finding(rule_id="same_person")])
    flagged = {field.label for field in finding.snapshot if field.flagged}
    assert flagged == {"Performed by", "Verified by"}
    assert finding.title == "Checked by the person who did it"


def test_a_blank_the_finding_is_about_reads_missing():
    finding = _only(
        [_finding(rule_id="missing_second_check", verified_by=None, verified_at=None)]
    )
    shown = {f.label: f.value for f in finding.snapshot}
    assert shown["Verified by"] == MISSING
    assert shown["Verified at"] == MISSING
    assert finding.missing_labels == ("Verified by", "Verified at")


def test_other_blanks_read_not_recorded_and_are_not_flagged():
    finding = _only(
        [
            _finding(
                rule_id="missing_performer",
                performed_by=None,
                verified_by=None,
                verified_at=None,
            )
        ]
    )
    by_label = {f.label: f for f in finding.snapshot}
    assert by_label["Performed by"].value == MISSING
    assert by_label["Verified by"].value == NOT_RECORDED
    assert not by_label["Verified by"].flagged
    assert finding.missing_labels == ("Performed by",)


def test_range_picture_only_for_out_of_range():
    assert _only([_finding()]).range is not None
    assert _only([_finding(rule_id="same_person")]).range is None


def test_range_view_places_reading_and_band():
    view = range_view(Decimal("3.6"), Decimal("2.5"), Decimal("3.5"), True)
    assert view is not None
    assert (view.band_left, view.band_width) == (25.0, 50.0)
    assert view.marker == 80.0
    assert not view.inside
    # Never placed on the master's scale when the unit differs.
    assert range_view(Decimal("3.6"), Decimal("2.5"), Decimal("3.5"), False) is None
    assert range_view(None, Decimal("2.5"), Decimal("3.5"), True) is None


def test_overview_counts_steps_and_findings_by_severity():
    findings = [
        _finding(),
        _finding(finding_id="f-2", finding_seq=1, rule_id="same_person"),
        _finding(
            finding_id="f-3",
            finding_seq=2,
            rule_id="missing_performer",
            step_severity="Needs Review",
            step_id="S-DISP-02",
            step_seq=2,
            unit_procedure="Dispensing",
        ),
    ]
    summary = overview(group_findings(findings, {}))
    assert (summary.findings, summary.steps) == (3, 2)
    assert (summary.critical_steps, summary.review_steps) == (1, 1)
    assert (summary.critical_findings, summary.review_findings) == (2, 1)
    assert summary.sentence == (
        "3 findings on 2 steps. By step: 1 Critical, 1 Needs Review."
    )


def test_overview_of_a_clean_run():
    summary = overview(())
    assert summary.rows == ()
    assert summary.sentence.startswith("No findings.")


def test_process_strip_marks_every_master_step():
    master = [
        StoredMasterStep(
            1,
            "S-DISP-01",
            "Dispensing",
            "Weigh",
            "Weigh API",
            "API net weight",
            "kg",
            None,
            None,
            True,
        ),
        StoredMasterStep(
            14,
            "S-COAT-02",
            "Coating",
            "Film coating",
            "Check coat",
            "Coating weight gain",
            "%",
            None,
            None,
            True,
        ),
    ]
    strip = process_strip(master, group_findings([_finding()], {}))
    assert [(s.step_id, s.status, s.anchor) for s in strip] == [
        ("S-DISP-01", "clean", None),
        ("S-COAT-02", "critical", "step-S-COAT-02"),
    ]
    assert strip[1].findings == ("Reading outside the allowed range",)


def test_glossary_and_faq_cover_each_page():
    keys = {term.key for term in GLOSSARY}
    assert len(keys) == len(GLOSSARY)
    for page, wanted in PAGE_TERMS.items():
        assert set(wanted) <= keys, page
        assert page_terms(page)
    assert page_terms("guide") == ()
    assert all(question.endswith("?") and answer for question, answer in FAQ)


def test_notes_stay_attached_to_their_finding():
    note = StoredNote(
        note_id="n-1",
        finding_id="f-1",
        reviewer_name="Reviewer A",
        note_text="Seen.",
        noted_at=datetime(2026, 11, 10, 14, 23, tzinfo=UTC),
    )
    (procedure,) = group_findings([_finding()], {"f-1": [note]})
    (shown,) = procedure.steps[0].findings[0].notes
    assert (shown.reviewer_name, shown.noted_at) == (
        "Reviewer A",
        "2026-11-10 14:23 UTC",
    )
