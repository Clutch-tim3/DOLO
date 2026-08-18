"""
A quotation never contains a price nobody supplied.

`/api/generate-quotation` synthesised prices in four places. A tender with no
extractable pricing produced a quotation for R798 116,25 — a figure from
nowhere — split 75/25 into two plausible-looking line items, typeset, and ready
to send to an organ of state:

    tender_val   = parsed_tender.get("tender_value") or 798116.25
    subtotal_est = float(tender_val) / 1.15
    unit_price   = round(subtotal_est * 0.75, 2)   # and * 0.25

The invented figure also chose the statute on the next line — 90/10 above R50m
— so a number from nowhere decided which law the bid was evaluated under.

This is the failure `agent/quotation/price_search.py` was rewritten to remove.

THE TRAP IN FIXING IT

Removing the invented number is not enough. With unit_price None the totals
rendered "R 0.00" and the executive summary asserted "a total evaluated bid
price of R 0.00" — which on a bid document is an offer to supply for nothing,
a definite wrong number rather than a plausible invented one. That was found by
generating the PDF and reading it, not by reading the code.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from models.quotation_generator import call_llm_for_proposal_summary, generate_quotation_pdf

APP = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
GENERATOR = (Path(__file__).resolve().parent.parent / "models" / "quotation_generator.py"
             ).read_text(encoding="utf-8")

#: The literals the endpoint used to return.
INVENTED = ("798116.25", "798,116.25")

UNPRICED = [{"description": "Supply and delivery per tender specification",
             "qty": 1, "unit_price": None}]


def _code_only(source: str) -> str:
    """Source with comment lines dropped — they quote the old values on purpose."""
    return "\n".join(l for l in source.splitlines() if not l.strip().startswith("#"))


# --- the literals are gone ----------------------------------------------------

@pytest.mark.parametrize("literal", INVENTED)
def test_the_invented_figure_is_not_in_the_endpoint(literal):
    assert literal not in _code_only(APP), f"{literal} is still produced"


def test_no_price_is_derived_from_a_tender_value():
    """
    `subtotal_est * 0.75` and `* 0.25` split an invented total into two lines
    that look considered. Deriving a price from a tender's own value is still
    inventing one.
    """
    code = _code_only(APP)
    assert "subtotal_est" not in code
    assert "* 0.75" not in code and "* 0.25" not in code


def test_no_competitor_price_is_derived_from_the_subtotal():
    """`subtotal * 0.90` made every supplier 11% above a rival who did not exist."""
    assert "subtotal * 0.90" not in _code_only(GENERATOR)


# --- the rendered document ----------------------------------------------------

def test_an_unpriced_quotation_contains_no_figure_at_all(tmp_path):
    """
    The one that matters, and the one only the rendered page can answer. Counts
    have lied on this project; documents have not.
    """
    from pypdf import PdfReader

    out = tmp_path / "unpriced.pdf"
    generate_quotation_pdf(
        supplier_info={"company_name": "Donington Vale",
                       "registration_number": "2020/123456/07", "bbbee_level": 2},
        tender_title="Supply and delivery of low voltage cabling",
        line_items=UNPRICED,
        output_path=out,
    )
    text = PdfReader(str(out)).pages[0].extract_text()

    figures = re.findall(r"R\s?[\d,]+\.\d{2}", text)
    assert not figures, f"an unpriced quotation printed money amounts: {figures}"

    for literal in INVENTED:
        assert literal not in text

    # R 0.00 is the specific wrong answer: on a bid document it is an offer to
    # supply for nothing, not a blank.
    assert "R 0.00" not in text
    assert "0.00" not in text


def test_the_unpriced_lines_and_totals_read_TBC(tmp_path):
    from pypdf import PdfReader

    out = tmp_path / "tbc.pdf"
    generate_quotation_pdf(
        supplier_info={"company_name": "Donington Vale", "bbbee_level": 2},
        tender_title="A tender", line_items=UNPRICED, output_path=out)
    text = PdfReader(str(out)).pages[0].extract_text()

    assert text.count("TBC") >= 3, "the line, the subtotal and the total must all read TBC"
    assert "incomplete" in text.lower(), "the document does not say it is incomplete"


def test_a_real_price_still_produces_a_real_quotation(tmp_path):
    """
    Withholding must not swallow the working case: a quotation with prices
    still totals correctly.
    """
    from pypdf import PdfReader

    out = tmp_path / "priced.pdf"
    generate_quotation_pdf(
        supplier_info={"company_name": "Donington Vale", "bbbee_level": 2},
        tender_title="A tender",
        line_items=[{"description": "Cabling", "qty": 2, "unit_price": 1000.0}],
        output_path=out,
    )
    text = PdfReader(str(out)).pages[0].extract_text()

    assert "R 2,000.00" in text          # 2 x 1000 line total
    assert "R 300.00" in text            # 15% VAT
    assert "R 2,300.00" in text          # total
    assert "incomplete" not in text.lower()


def test_a_mixed_quotation_excludes_the_unpriced_line_from_the_total(tmp_path):
    """Counting an unpriced line as zero would produce a total that looks complete."""
    from pypdf import PdfReader

    out = tmp_path / "mixed.pdf"
    generate_quotation_pdf(
        supplier_info={"company_name": "Donington Vale", "bbbee_level": 2},
        tender_title="A tender",
        line_items=[{"description": "Cabling", "qty": 1, "unit_price": 1000.0},
                    {"description": "Installation", "qty": 1, "unit_price": None}],
        output_path=out,
    )
    text = PdfReader(str(out)).pages[0].extract_text()

    assert "TBC" in text
    assert "incomplete" in text.lower()
    # Totals are withheld entirely rather than reporting only the priced half
    # as though it were the whole quotation.
    assert "R 1,150.00" not in text


# --- the prose ----------------------------------------------------------------

def test_the_summary_states_no_total_when_there_is_none():
    """
    The fallback path runs whenever no LLM key is set, so it reaches most
    documents — and it was the one that printed "R 0.00" in prose.
    """
    summary = call_llm_for_proposal_summary("Donington Vale", "A tender", None)
    assert not re.findall(r"R\s?[\d,]+\.\d{2}", summary)
    assert "0.00" not in summary
    assert "not yet been completed" in summary.lower()


def test_the_summary_still_states_a_real_total():
    summary = call_llm_for_proposal_summary("Donington Vale", "A tender", 2300.0)
    assert "2,300.00" in summary
