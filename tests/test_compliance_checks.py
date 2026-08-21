"""
The checks a procurement officer runs before reading the proposal.

`SBD_COMPLIANCE.md`: "Administrative mistakes disqualify more South African
tender submissions than weak pricing or poor technical proposals... CairoAI
fills these forms. That puts it in a position to catch every one of those
failures before submission, and it currently catches none of them."

None of this fills anything or blocks anything. `review_gate.export_reviewed`
remains the only thing that refuses an export — a second gate would be a second
opinion, and two things able to refuse will disagree eventually.
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_autofill.integration.compliance_checks import (
    cross_form_conflicts,
    disqualification_summary,
    expiry_problems,
    find_closing_date,
    parse_date,
    signature_tasks,
)

PACK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "archive", "temp_tender_BID_DOCUMENT_06FY27_.pdf")


class _Field:
    def __init__(self, label, value=None, reason="", location="page 1",
                 canonical_field=None):
        self.label, self.value, self.reason = label, value, reason
        self.location, self.canonical_field = location, canonical_field


class _Result:
    def __init__(self, filled=(), skipped=()):
        self.filled, self.skipped = list(filled), list(skipped)


# --- dates ----------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("18 August 2026", date(2026, 8, 18)),
    ("22-March-2027", date(2027, 3, 22)),          # his B-BBEE certificate
    ("31 Aug 2026", date(2026, 8, 31)),
    ("August 18, 2026", date(2026, 8, 18)),
    ("2026-08-18", date(2026, 8, 18)),
    ("2026/08/18", date(2026, 8, 18)),
])
def test_the_date_formats_sa_tenders_use(text, expected):
    assert parse_date(text) == expected


def test_ambiguous_numeric_dates_are_read_day_first():
    """
    SA convention. Reading 08/09/2026 as 9 August would report a certificate
    valid a month after it expired.
    """
    assert parse_date("08/09/2026") == date(2026, 9, 8)


def test_an_impossible_date_is_not_a_date():
    assert parse_date("31 February 2026") is None
    assert parse_date("") is None
    assert parse_date("no date here") is None


@pytest.mark.skipif(not os.path.exists(PACK), reason="the 145-page pack is not present")
def test_the_real_closing_date_is_found():
    """"...in the TENDER BOX not later than 11:00 on 18 August 2026"."""
    import fitz

    with fitz.open(PACK) as doc:
        text = "\n".join(page.get_text() for page in doc)
    assert find_closing_date(text) == date(2026, 8, 18)


def test_no_closing_date_is_an_answer():
    assert find_closing_date("A tender for fencing.") is None


# --- P0-3 · validity at closing --------------------------------------------------

def test_a_certificate_expiring_before_closing_is_raised():
    problems = expiry_problems(date(2026, 8, 18), [
        {"document_type": "B-BBEE certificate", "expiry_date": "01 August 2026"},
    ])
    assert len(problems) == 1
    assert problems[0]["severity"] == "expired"
    assert "scores zero" in problems[0]["message"]


def test_a_certificate_valid_at_closing_is_not_raised():
    """His expires 22 March 2027; this tender closes 18 August 2026."""
    assert expiry_problems(date(2026, 8, 18), [
        {"document_type": "B-BBEE certificate", "expiry_date": "22-March-2027"},
    ]) == []


def test_expiring_soon_after_closing_is_worth_saying():
    """The brief: "'Expires in 3 weeks, this tender closes in 5' is worth saying too.\""""
    problems = expiry_problems(date(2026, 8, 18), [
        {"document_type": "SARS TCS PIN", "expiry_date": "2026-09-01"},
    ])
    assert problems and problems[0]["severity"] == "expiring"


def test_without_a_closing_date_nothing_is_compared():
    """
    Comparing against today instead would silently clear an expired certificate
    on a tender that closed last month. Today is not the deadline.
    """
    assert expiry_problems(None, [
        {"document_type": "B-BBEE certificate", "expiry_date": "01 January 2020"},
    ]) == []


def test_a_document_with_no_expiry_is_not_invented():
    assert expiry_problems(date(2026, 8, 18), [
        {"document_type": "CIPC COR14.3", "expiry_date": None},
    ]) == []


# --- P0-1 · the same fact on every form ------------------------------------------

def test_the_same_field_written_two_ways_is_caught():
    """A named disqualification cause: registration number differing across forms."""
    conflicts = cross_form_conflicts([
        _Field("Registration No", "2016/123456/07", location="page 2",
               canonical_field="registration_number"),
        _Field("Company registration number", "2016/999999/07", location="page 9",
               canonical_field="registration_number"),
    ])
    assert len(conflicts) == 1
    assert {v["value"] for v in conflicts[0]["values"]} == {
        "2016/123456/07", "2016/999999/07"}


def test_formatting_differences_are_not_conflicts():
    """
    "2016/123456/07" and "2016 123456 07" are one value. Reporting those would
    bury the real mismatches.
    """
    assert cross_form_conflicts([
        _Field("Reg No", "2016/123456/07", canonical_field="registration_number"),
        _Field("Registration number", "2016 123456 07",
               canonical_field="registration_number"),
    ]) == []


def test_agreement_is_silent():
    assert cross_form_conflicts([
        _Field("Name", "Donington Vale (Pty) Ltd", canonical_field="company_name"),
        _Field("Name of Bidder", "Donington Vale (Pty) Ltd",
               canonical_field="company_name"),
    ]) == []


# --- P0-4 · what would disqualify this bid ----------------------------------------

def test_signature_lines_are_counted_from_the_record_not_the_page():
    """
    The brief counts `[ ! ]` marks. The owner had those removed — they survive
    printing and he was handing organs of state marked-up statutory forms. The
    refusal record was always the real source.
    """
    tasks = signature_tasks([
        _Field("SIGNATURE OF BIDDER", reason="Requires your signature",
               location="page 3"),
        _Field("CELL NUMBER", reason="Nothing on file for this field yet",
               location="page 1"),
    ])
    assert len(tasks) == 1
    assert tasks[0]["location"] == "page 3"


def test_the_summary_leads_with_what_would_disqualify():
    """
    "A user who reads nothing else should still see the four signatures they
    have to add."
    """
    result = _Result(skipped=[
        _Field(f"SIGNATURE {n}", reason="Requires your signature",
               location=f"page {n}") for n in (7, 117, 38)
    ])
    summary = disqualification_summary(result, {})

    assert summary["would_disqualify"], "a pack needing 3 signatures says so"
    assert "3 signature line(s)" in summary["would_disqualify"][0]


def test_pages_are_listed_in_reading_order():
    """A person works through a pack front to back, so 7 comes before 117."""
    result = _Result(skipped=[
        _Field(f"SIGNATURE {n}", reason="Requires your signature",
               location=f"page {n}") for n in (117, 7, 38)
    ])
    line = disqualification_summary(result, {})["would_disqualify"][0]
    assert line.index("page 7") < line.index("page 38") < line.index("page 117")


def test_multiple_directors_are_flagged_where_signatures_are_needed():
    """One missing director's signature disqualifies the whole submission."""
    result = _Result(skipped=[
        _Field("SIGNATURE", reason="Requires your signature", location="page 3")])
    summary = disqualification_summary(
        result, {"directors": [{"name": "A"}, {"name": "B"}]})

    assert any("2 directors" in line for line in summary["would_disqualify"])


def test_a_claimed_goal_reminds_you_to_attach_the_certificate():
    """A B-BBEE claim without the certificate scores zero."""
    summary = disqualification_summary(
        _Result(), {}, goal_proposals=[{"action": "claim"}])
    assert any("scores zero" in line for line in summary["would_disqualify"])


def test_a_clean_draft_says_so_plainly():
    summary = disqualification_summary(_Result(filled=[_Field("Name", "X")]), {})
    assert summary["would_disqualify"] == []
    assert "No administrative problems" in summary["message"]


def test_the_summary_never_refuses_anything():
    """
    It reports. `review_gate.export_reviewed` is the only thing that refuses an
    export, and putting a second gate here would guarantee they disagree.
    """
    summary = disqualification_summary(
        _Result(skipped=[_Field("SIGNATURE", reason="Requires your signature")]), {})
    assert "blocked" not in summary
    assert "refused" not in summary
    assert set(summary) >= {"would_disqualify", "message"}
