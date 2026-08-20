"""
Pre-printed content and prose are not offered as fields.

On the owner's 145-page pack the extractor proposed 651 blanks. Eight of them
fill. 534 were refused as unreadable or unrecognised — and every refusal draws
a red [ ! ] on the page, so the document came back with 534 markers on it and a
flag list nobody could work through.

They were never fields:

    POINTS · 80 · 20 · 100      the points-allocation table an organ of state
                                completes BEFORE issuing the tender
    "of this tender"            fragments of the surrounding instructions
    "the tenderer)"
    (no label at all)           164 detected blanks with nothing to read

NOTHING HERE CHANGES WHAT GETS FILLED — measured, not assumed. The same eight
fields fill before and after; 651 blanks become 320.

CONSERVATIVE ON PURPOSE. "Name of Bidder (tenderer)" fills successfully and
ends in a bracket, and "Value of work inclusive of VAT (Rand)" is a real column
header. A rule as crude as "ends with )" would drop real fields, so most of
this file is about what must NOT be rejected.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_autofill.extraction import layout_blank_extractor as ext

REAL_PACK = Path(__file__).resolve().parent.parent / "data" / "archive" / "temp_tender_BID_DOCUMENT_06FY27_.pdf"

is_fillable_candidate = getattr(ext, "is_fillable_candidate", None)
needs_filter = pytest.mark.skipif(is_fillable_candidate is None,
                                  reason="candidate filter not implemented")


class _Blank:
    def __init__(self, label):
        self.label_text = label


# --- what must be rejected ----------------------------------------------------

@needs_filter
def test_a_blank_with_no_label_is_not_a_field():
    """
    164 of the owner's 651. The fill engine could only ever refuse them with
    "could not read what this field is for" — true, and useless to a reader.
    """
    for empty in (None, "", "   ", "\n"):
        assert not is_fillable_candidate(_Blank(empty))


@needs_filter
@pytest.mark.parametrize("label", ["80", "20", "100", "  100  ", "80%", "1,000", "R 500"])
def test_the_points_allocation_table_is_not_a_field(label):
    """
    On SBD 6.1 these are the preference point split, filled in by the organ of
    state before the tender is issued. Offering them as blanks invites a
    supplier to overwrite the evaluation criteria.
    """
    assert not is_fillable_candidate(_Blank(label))


@needs_filter
@pytest.mark.parametrize("label", [
    "of", "and", "the", "to", "in", "I", "of.", "and,",
])
def test_a_bare_function_word_is_a_mis_split(label):
    assert not is_fillable_candidate(_Blank(label))


@needs_filter
@pytest.mark.parametrize("label", [
    "I, the undersigned,",
    "do hereby declare, in my capacity as",
    "representative of (tenderer)",
    "in my capacity as director",
    "on behalf of the tenderer",
    "Note: this must be completed in full",
    "It is a condition of this tender that",
])
def test_a_declaration_preamble_is_prose_not_a_prompt(label):
    """The sworn-statement preambles on SBD 4 and SBD 6.1."""
    assert not is_fillable_candidate(_Blank(label))


@needs_filter
@pytest.mark.parametrize("label", [
    "DETAILED IN THE EVALUATION CRITERIA.]",
    "Stipulated minimum threshold for Local content,",
    "the successful bidder;",
    "as set out below.)",
])
def test_an_instruction_cut_off_mid_clause_is_rejected(label):
    """A real label does not end mid-clause."""
    assert not is_fillable_candidate(_Blank(label))


@needs_filter
def test_a_lowercase_sentence_fragment_is_rejected():
    assert not is_fillable_candidate(_Blank("of this tender"))
    assert not is_fillable_candidate(_Blank("ownership and share certificate where applicable"))


# --- what must NOT be rejected ------------------------------------------------

@needs_filter
@pytest.mark.parametrize("label", [
    "Name of Bidder (tenderer)",       # fills successfully today
    "Value of work inclusive of VAT (Rand)",
    "NAME OF TENDERER",
    "CSD NUMBER",
    "E-MAIL ADDRESS",
    "Company registration number",
    "VAT REGISTRATION NUMBER",
    "TAX COMPLIANCE SYSTEM PIN",
    "Description of contract",
    "Date contract started",
    "Employer, contact person and telephone number",
    "Full Name",
    "ADDRESS",
    "Name of State institution",
])
def test_a_real_field_survives(label):
    """
    The whole risk in this change. Two of these end in a bracket, one contains
    a comma mid-label, and several are single words — all of them are genuine
    questions on the owner's pack.
    """
    assert is_fillable_candidate(_Blank(label)), f"{label!r} would be dropped"


# --- against the owner's pack -------------------------------------------------

@needs_filter
@pytest.mark.skipif(not REAL_PACK.exists(), reason="the owner's pack is not present")
def test_the_noise_goes_and_the_fills_do_not():
    """
    The measurement this change rests on: the same eight fields fill, and a
    third of the proposed blanks — every one of which was already being
    refused — stops being proposed at all.
    """
    import tempfile

    from agent_autofill.extraction import match_label
    from agent_autofill.fill_engine.pdf_filler import fill_pdf

    profile = {"company_name": "DONINGTON VALE (PTY) LTD",
               "registration_number": "2020/654321/07",
               "csd_number": "MAAA1234567",
               "standard_email": "a@b.co.za"}

    with tempfile.TemporaryDirectory() as tmp:
        result = fill_pdf(REAL_PACK, Path(tmp) / "f.pdf", profile, match_label)

    labels = {(f.label or "").strip() for f in result.filled}
    assert "NAME OF TENDERER" in labels
    assert "CSD NUMBER" in labels
    assert "Name of Bidder (tenderer)" in labels
    assert len(result.filled) >= 8, f"fills dropped to {len(result.filled)}"

    # And the page is no longer covered in markers.
    assert len(result.skipped) < 400, (
        f"{len(result.skipped)} refusals still draw a marker on the page"
    )


@needs_filter
@pytest.mark.skipif(not REAL_PACK.exists(), reason="the owner's pack is not present")
def test_unlabelled_blanks_no_longer_reach_the_fill_engine():
    blanks = ext.extract_pdf_blanks(REAL_PACK)
    unlabelled = [b for b in blanks if not (b.label_text or "").strip()]
    assert not unlabelled, f"{len(unlabelled)} blanks with no label are still proposed"
