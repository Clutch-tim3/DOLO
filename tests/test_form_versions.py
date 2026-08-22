"""
Which forms are in the pack, and which edition — golden rule 4.

    "Never use old forms — always use the exact forms from the tender pack."

`Comprehensive_Tender_Document_Training_Guide.pdf`:

    CRITICAL UPDATE (Effective 31 March 2022): The new SBD 4: Bidder's
    Disclosure replaced the old SBD 4 (Declaration of Interest), SBD 8 (Past
    SCM Practices), and SBD 9 (Independent Bid Determination) into one
    consolidated form. However, some departments still use the old separate
    forms — always use the exact forms from the tender pack.

So "SBD 4" names two different documents. The pre-2022 one covers conflicts of
interest only; the consolidated one also carries past SCM practices and the
independent-bid certification.

This REPORTS. It changes no fill decision, because substituting a form from
another pack is the exact failure golden rule 4 names.
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_autofill.classification.form_versions import (
    CONSOLIDATED,
    LEGACY,
    UNKNOWN,
    describe_pack,
    forms_in,
    sbd4_edition,
)

JW = r"C:\Users\Thabang\Downloads\autofill_988c1244_RFQJW087KM26_3fb61e5f.pdf"
PACK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "archive", "temp_tender_BID_DOCUMENT_06FY27_.pdf")


def _text(path):
    import fitz

    with fitz.open(path) as doc:
        return "\f".join(page.get_text() for page in doc)


# --- finding the forms ----------------------------------------------------------

def test_forms_are_found_with_their_page():
    found = forms_in("cover\fsome preamble\fSBD 1: Invitation to Bid")
    assert found["SBD 1"]["first_seen"] == 3


def test_municipal_forms_count_too():
    """Municipalities issue MBD. Same numbering, same content."""
    assert "MBD 6.1" in forms_in("MBD 6.1 preference points")


def test_decimal_form_numbers_survive():
    names = set(forms_in("SBD 3.1 and SBD 6.2 and SBD 7.3"))
    assert names == {"SBD 3.1", "SBD 6.2", "SBD 7.3"}


def test_the_first_page_a_form_appears_on_is_kept():
    """A form quoted again later must not move its page reference."""
    found = forms_in("SBD 4 here\fnothing\fSBD 4 again")
    assert found["SBD 4"]["first_seen"] == 1


# --- which SBD 4 ----------------------------------------------------------------

def test_the_consolidated_edition_is_recognised():
    assert sbd4_edition("SBD 4: Bidder's Disclosure") == CONSOLIDATED
    assert sbd4_edition("DECLARATION OF PAST SCM PRACTICES") == CONSOLIDATED
    assert sbd4_edition(
        "Certificate of Independent Bid Determination") == CONSOLIDATED


def test_the_legacy_edition_is_recognised():
    assert sbd4_edition("SBD 4: DECLARATION OF INTEREST") == LEGACY


def test_declaration_of_interest_alone_does_not_mean_legacy():
    """
    THE trap. Part A of the consolidated form is still headed "Declaration of
    Interest", so those words prove nothing on their own — while the past-SCM
    and independent-bid headings exist only in the consolidated edition.
    """
    both = ("SBD 4: Bidder's Disclosure\nPART A: Declaration of Interest\n"
            "PART B: Declaration of Past SCM Practices")
    assert sbd4_edition(both) == CONSOLIDATED


def test_saying_nothing_is_not_a_guess():
    assert sbd4_edition("A tender for fencing.") == UNKNOWN
    assert sbd4_edition("") == UNKNOWN


def test_no_edition_is_reported_when_there_is_no_sbd_4():
    assert describe_pack("SBD 1 and SBD 6.1 only")["sbd4_edition"] is None


# --- what the user is told ------------------------------------------------------

def test_a_legacy_pack_says_what_is_missing_from_it():
    out = describe_pack("SBD 4: DECLARATION OF INTEREST")
    assert out["notes"]
    assert "SBD 8" in out["notes"][0] and "SBD 9" in out["notes"][0]


def test_a_clean_consolidated_pack_says_nothing():
    """Silence means nothing unusual, not that the pack is complete."""
    assert describe_pack("SBD 4: Bidder's Disclosure\fSBD 1")["notes"] == []


def test_a_mixed_pack_gets_one_note_not_two():
    """
    Real, from the owner's Johannesburg Water RFQ: it carries MBD 4
    (consolidated) AND MBD 8 and MBD 9, the two it replaced. The mixed note
    says everything the legacy note would, so they must not both fire.
    """
    out = describe_pack("MBD 4 Bidder's Disclosure\fMBD 8\fMBD 9")
    assert len(out["notes"]) == 1
    assert "mixes editions" in out["notes"][0]


def test_no_note_ever_suggests_substituting_a_form():
    """
    Golden rule 4, as a property rather than a phrasing. Every note points the
    user back at the pack in front of them, and none tells them to fetch the
    current template, download a newer version, or replace what they were sent.
    """
    for text in ("SBD 4: DECLARATION OF INTEREST",
                 "MBD 4 Bidder's Disclosure\fMBD 8\fMBD 9"):
        note = describe_pack(text)["notes"][0]
        assert "pack" in note, "a note must anchor the user to their own pack"
        lowered = note.lower()
        for bad in ("download the", "use the current", "replace it with",
                    "use the latest", "get the new"):
            assert bad not in lowered, f"note suggests substitution: {bad!r}"


# --- against the real documents -------------------------------------------------

@pytest.mark.skipif(not os.path.exists(PACK), reason="the 145-page pack is not present")
def test_the_real_pack_is_read():
    out = describe_pack(_text(PACK))
    assert {"SBD 1", "SBD 4", "SBD 6.1", "SBD 7"} <= set(out["form_names"])
    assert out["sbd4_edition"] == CONSOLIDATED
    assert out["notes"] == [], "nothing unusual about this pack"


@pytest.mark.skipif(not os.path.exists(JW), reason="the JW RFQ is not present")
def test_the_johannesburg_water_pack_mixes_editions():
    """Found by running this against a live pack, not constructed for the test."""
    out = describe_pack(_text(JW))
    assert {"MBD 4", "MBD 8", "MBD 9"} <= set(out["form_names"])
    assert any("mixes editions" in n for n in out["notes"])


# --- expiries fixed by rule ------------------------------------------------------

def test_coida_expires_on_31_march_whatever_the_letter_says():
    """
    "COIDA Letter of Good Standing is current (expires 31 March annually)".
    A letter issued in December is good for three months, not twelve.
    """
    from agent_autofill.integration.compliance_checks import implied_expiry

    expiry = implied_expiry("COIDA Letter of Good Standing", None)
    assert expiry is not None
    assert (expiry.month, expiry.day) == (3, 31)
    assert expiry >= date.today()


def test_an_affidavit_expires_twelve_months_from_the_oath():
    """"Affidavits expire 12 months from Commissioner of Oaths date"."""
    from agent_autofill.integration.compliance_checks import implied_expiry

    assert implied_expiry("EME sworn affidavit", date(2026, 3, 23)) == date(2027, 3, 23)


def test_a_leap_day_oath_lands_on_the_28th():
    from agent_autofill.integration.compliance_checks import implied_expiry

    assert implied_expiry("EME affidavit", date(2028, 2, 29)) == date(2029, 2, 28)


def test_no_rule_means_no_invented_expiry():
    """
    Inventing one would let CairoAI declare a valid certificate dead, or imply
    it checked something it did not.
    """
    from agent_autofill.integration.compliance_checks import implied_expiry

    assert implied_expiry("CIPC COR14.3", date(2026, 1, 1)) is None
    assert implied_expiry("B-BBEE certificate", None) is None
    assert implied_expiry("EME affidavit", None) is None, "no oath date, no rule"


# --- points being left on the table ----------------------------------------------

def test_an_eme_below_the_threshold_is_told_about_level_1():
    """
    "EME (turnover <R10m) with 51%+ Black ownership = automatic Level 1 (20
    points)." Sitting on Level 4 costs eight points under 80/20.
    """
    from agent_autofill.integration.compliance_checks import eme_level_1_note

    note = eme_level_1_note({"annual_turnover": 4_500_000,
                             "owned_51pc_black": True, "bbbee_level": 4})
    assert note and "Level 1" in note
    assert "R10 000 000" in note, "the threshold reads as rands, not a decimal"


def test_it_never_changes_the_level_itself():
    """
    "Claimed level MUST match your certificate. Mismatch = disqualification,
    not correction." So this raises it and stops.
    """
    from agent_autofill.integration.compliance_checks import eme_level_1_note

    note = eme_level_1_note({"annual_turnover": 4_500_000,
                             "owned_51pc_black": True, "bbbee_level": 4})
    assert "will not change the level" in note
    assert "affidavit" in note


def test_a_company_already_at_level_1_is_not_nagged():
    from agent_autofill.integration.compliance_checks import eme_level_1_note

    assert eme_level_1_note({"annual_turnover": 4_500_000,
                             "owned_51pc_black": True, "bbbee_level": 1}) is None


def test_above_the_threshold_the_rule_does_not_apply():
    from agent_autofill.integration.compliance_checks import eme_level_1_note

    assert eme_level_1_note({"annual_turnover": 12_000_000,
                             "owned_51pc_black": True, "bbbee_level": 4}) is None


def test_without_black_ownership_it_does_not_apply():
    from agent_autofill.integration.compliance_checks import eme_level_1_note

    assert eme_level_1_note({"annual_turnover": 4_500_000,
                             "owned_51pc_black": False, "bbbee_level": 4}) is None


def test_an_unknown_turnover_says_nothing():
    """Not knowing is not the same as qualifying."""
    from agent_autofill.integration.compliance_checks import eme_level_1_note

    assert eme_level_1_note({"owned_51pc_black": True, "bbbee_level": 4}) is None


def test_an_opportunity_is_not_a_disqualification():
    """
    Kept in its own list. Points left on the table must not dilute the list of
    things that would get the bid thrown out.
    """
    from agent_autofill.integration.compliance_checks import disqualification_summary

    result = type("R", (), {"filled": [], "skipped": []})()
    summary = disqualification_summary(
        result, {"annual_turnover": 4_500_000, "owned_51pc_black": True,
                 "bbbee_level": 4})

    assert summary["opportunities"]
    assert summary["would_disqualify"] == []
