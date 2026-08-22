"""
Which procurement system a document is from, before applying anyone's rules.

CairoAI was built for South African government tenders and applies their rules
to everything it opens. `Comprehensive_Tender_Document_Training_Guide.pdf` sets
three systems side by side in its Part 4 table and the columns disagree.

Recognising the system comes before supporting it. A UN ITB has no CSD number,
no B-BBEE level, no CIDB grade and no COIDA letter, so a bidder told to check
theirs is being sent after something that does not exist in that tender.

THE DIFFERENCE THAT COSTS A BID

    Price in technical bid   SA:  combined, price visible
                             UN:  MUST NOT appear in the technical part
                             WB:  MUST NOT appear in the technical part

Someone used to SA packs, where the price sits on SBD 3 inside the same
submission, puts a figure in a UN technical proposal and is disqualified before
anything is evaluated.

This module REPORTS. No fill decision reads it, so a misdetection costs a wrong
note on a review screen and never a wrong value on a form.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_autofill.classification.procurement_system import (
    RULES,
    SOUTH_AFRICA,
    UNGM,
    UNKNOWN,
    WORLD_BANK,
    detect,
)

PACK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "archive", "temp_tender_BID_DOCUMENT_06FY27_.pdf")


def _text(path):
    import fitz

    with fitz.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


# --- recognising each system -----------------------------------------------------

@pytest.mark.parametrize("text", [
    "SBD 1: Invitation to Bid",
    "Your CSD supplier number and B-BBEE status level",
    "in terms of the PFMA and National Treasury regulations",
    "CIDB grading designation 3SQ and a COIDA letter of good standing",
])
def test_south_african_documents_are_recognised(text):
    assert detect(text)["system"] == SOUTH_AFRICA


@pytest.mark.parametrize("text", [
    "Vendors must be registered on UNGM",
    "United Nations Global Marketplace registration required",
    "Provide your UNSPSC commodity codes",
    "UNDP Invitation to Bid",
])
def test_un_documents_are_recognised(text):
    assert detect(text)["system"] == UNGM


@pytest.mark.parametrize("text", [
    "World Bank Standard Procurement Document",
    "IBRD financing, see the Bid Data Sheet",
    "ESHS Declaration Form",
    "Beneficial Ownership Disclosure Form",
])
def test_world_bank_documents_are_recognised(text):
    assert detect(text)["system"] == WORLD_BANK


def test_generic_procurement_words_prove_nothing():
    """
    "bidder", "tender" and "procurement" appear in all three systems. Matching
    on those would put a confident label on every document.
    """
    out = detect("The bidder must submit a tender for this procurement.")
    assert out["system"] == UNKNOWN
    assert out["evidence"] == []


def test_silence_is_unknown():
    assert detect("")["system"] == UNKNOWN
    assert detect(None)["system"] == UNKNOWN


# --- being honest about what is supported ----------------------------------------

def test_a_non_south_african_document_says_so_plainly():
    """
    CairoAI's field vocabulary, blocklist and compliance checks are all built
    around SBD forms. Saying otherwise would be the wrong kind of confident.
    """
    out = detect("UNDP Invitation to Bid, register on UNGM")
    assert out["support"] == "recognised_only"
    assert any("South African" in n for n in out["notes"])


def test_a_south_african_document_does_not_get_that_warning():
    out = detect("SBD 1 and your CSD number and B-BBEE level")
    assert out["support"] == "full"
    assert not any("South African" in n for n in out["notes"])


def test_an_unknown_document_is_treated_as_unsupported():
    out = detect("A document about nothing in particular.")
    assert out["support"] == "recognised_only"
    assert out["notes"], "an unrecognised document must not pass silently"


# --- the rules that differ -------------------------------------------------------

@pytest.mark.parametrize("system", [UNGM, WORLD_BANK])
def test_the_price_rule_leads_for_both_two_envelope_systems(system):
    """
    The single most expensive habit to carry over from a South African pack,
    so it is the first thing said, in capitals.
    """
    first = RULES[system][0]
    assert "PRICE MUST NOT APPEAR IN THE TECHNICAL PART" in first


def test_the_south_african_rules_do_not_claim_price_is_separate():
    """SA bids are combined and the price IS visible. Saying otherwise would be
    wrong advice given confidently."""
    joined = " ".join(RULES[SOUTH_AFRICA])
    assert "MUST NOT APPEAR" not in joined


def test_un_documents_carry_the_no_modification_rule():
    joined = " ".join(RULES[UNGM])
    assert "No deletion or modification" in joined


def test_the_bid_data_sheet_override_is_stated_for_world_bank():
    """"Project-specific values that override Section 2. CRITICAL: Every number
    here governs your bid.\""""
    joined = " ".join(RULES[WORLD_BANK])
    assert "Bid Data Sheet overrides" in joined


def test_unknown_carries_no_rules():
    """Better nothing than another system's rules applied on a guess."""
    assert detect("nothing in particular")["rules"] == []


# --- close calls stay visible ----------------------------------------------------

def test_a_document_matching_two_systems_reports_both():
    """
    A donor-funded South African tender genuinely is both. Resolving that
    silently would hide the question of whose rules apply.
    """
    out = detect("SBD 1 and CSD number, financed by the World Bank under IDA, "
                 "see the Bid Data Sheet and the ESHS Declaration Form")
    assert out["also_matched"], "the runner-up must be reported"
    assert any("more than one procurement system" in n for n in out["notes"])


def test_the_stronger_match_wins():
    heavy_sa = ("SBD 1, SBD 4, SBD 6.1, CSD supplier number, B-BBEE status "
                "level, CIDB grading, COIDA, PFMA and National Treasury")
    out = detect(heavy_sa + " and one mention of the World Bank")
    assert out["system"] == SOUTH_AFRICA


def test_confidence_reflects_how_clean_the_match_was():
    clean = detect("SBD 1, CSD number, B-BBEE level, CIDB, PFMA")
    mixed = detect("SBD 1 and CSD number and the World Bank and IDA and UNGM "
                   "and UNSPSC and ESHS and Bid Data Sheet")
    assert clean["confidence"] > mixed["confidence"]


# --- against the real documents --------------------------------------------------

@pytest.mark.skipif(not os.path.exists(PACK), reason="the 145-page pack is not present")
def test_the_owners_real_pack_is_south_african():
    out = detect(_text(PACK))
    assert out["system"] == SOUTH_AFRICA
    assert out["support"] == "full"
