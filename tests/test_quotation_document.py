"""
Rendering a quotation for a company that is actually filled in.

Every profile these tests were originally written against was a fixture with
most fields empty, which hid a whole class of bug: `directors` is a JSON field
that company_store decodes into a list of dicts, the renderer read it as a
string, and the first real company profile could not produce a quotation at
all.

So the profile used here is shaped like a real one — populated, with directors
as the list the store actually returns.
"""

from __future__ import annotations

import pytest

pytest.importorskip("reportlab", reason="reportlab renders the quotation")
fitz = pytest.importorskip("fitz", reason="PyMuPDF reads the result back")

from agent.quotation.quote_document import render_quotation
from agent.quotation.quote_theme import DEFAULT_BRAND, palette

#: Shaped like `get_company_profile` returns it, including directors already
#: decoded from JSON into a list.
COMPANY = {
    "company_name": "Donington Vale (Pty) Ltd",
    "tagline": "AI Solutions & Venture Holdings",
    "registration_number": "2026/250499/07",
    "csd_number": "MAAA1714262",
    "physical_address": "10950 Nokukwane Street, Centurion, 0187",
    "standard_phone": "083 491 0088",
    "authorized_signatory_name": "Thabang Molwantwa",
    "authorized_signatory_capacity": "Director",
    "brand_colour": "#40220a",
    "directors": [{"name": "Thabang Molwantwa", "id_number": "0000000000000"}],
}

CLIENT = {"organisation": "National Health Laboratory Service",
          "attention": "Procurement Officer", "salutation": "Dear Sir,"}

ITEMS = [{"description": "Annual service and calibration", "serial": "SR082213",
          "qty": 1, "unit_price": 1402.81}]


def _render(tmp_path, company=None, items=None, name="q.pdf"):
    out = tmp_path / name
    render_quotation(out, company=company or COMPANY, client=CLIENT,
                     reference="RFQ 1", subject="Test subject",
                     line_items=items if items is not None else ITEMS,
                     date_text="19 August 2026")
    return out


def _text(path):
    doc = fitz.open(str(path))
    try:
        return doc[0].get_text()
    finally:
        doc.close()


# --- the regression --------------------------------------------------------


def test_a_company_with_directors_can_render(tmp_path):
    """THE BUG. `directors` is a list of dicts, not a string.

        directors = (company.get("directors") or "").strip()
        AttributeError: 'list' object has no attribute 'strip'

    Every profile tested against had the field empty, so this only surfaced
    when a real company was filled in properly — at which point quotations
    stopped working entirely for them.
    """
    text = _text(_render(tmp_path))
    assert "THABANG MOLWANTWA" in text.upper()


def test_a_directors_id_number_never_reaches_the_page(tmp_path):
    """Names belong on a letterhead. ID numbers are on the profile for SBD 4."""
    text = _text(_render(tmp_path))
    assert "0000000000000" not in text


def test_directors_stored_as_a_plain_string_still_work(tmp_path):
    """Older profiles hold a bare string; a migration must not lose a footer."""
    company = {**COMPANY, "directors": "Thabang Molwantwa"}
    assert "THABANG MOLWANTWA" in _text(_render(tmp_path, company)).upper()


def test_no_directors_is_not_an_error(tmp_path):
    company = {**COMPANY, "directors": []}
    text = _text(_render(tmp_path, company))
    assert "2026/250499/07" in text          # the footer still carries the reg


# --- VAT is charged only when it is due ------------------------------------


def test_a_company_with_no_vat_number_is_not_charged_vat(tmp_path):
    """
    The generator this replaced added `subtotal * 0.15` unconditionally, which
    charged tax a non-registered supplier cannot legally collect, on a document
    addressed to an organ of state.
    """
    text = _text(_render(tmp_path))
    assert "not currently a VAT-registered vendor" in text
    assert "VAT (15%)" not in text


def test_a_vat_registered_company_is(tmp_path):
    company = {**COMPANY, "vat_registration_number": "4480290011"}
    text = _text(_render(tmp_path, company))
    assert "VAT (15%)" in text
    assert "4480290011" in text


# --- a price nobody supplied -----------------------------------------------


def test_a_line_with_no_price_is_TBC_and_not_counted(tmp_path):
    """
    price_search returns no prices by design. A quotation that quietly invents
    one is the worst document this system could produce, so an unpriced line
    must be visible as unpriced.
    """
    items = [{"description": "Priced", "qty": 1, "unit_price": 100.0},
             {"description": "Not priced", "qty": 1}]
    text = _text(_render(tmp_path, items=items))
    assert "TBC" in text
    assert "incomplete" in text.lower()
    # The total is the priced line alone, not a guess at the other.
    assert "R100,00" in text


# --- branding --------------------------------------------------------------


def test_the_brand_colour_drives_the_document(tmp_path):
    """One colour in, a whole palette out — the band is derived, not raw."""
    pal = palette(COMPANY["brand_colour"])
    assert pal["band"] == "#40220a"
    assert pal["accent"] != pal["band"] and pal["rule"] != pal["band"]


def test_no_brand_colour_falls_back_to_olive(tmp_path):
    """A company that has not chosen a colour still gets a deliberate document."""
    assert palette(None)["band"] == DEFAULT_BRAND.lower()
    assert palette("not a colour")["band"] == DEFAULT_BRAND.lower()
    _render(tmp_path, {**COMPANY, "brand_colour": ""})   # must still render


def test_a_missing_logo_does_not_stop_the_quotation(tmp_path):
    company = {**COMPANY, "logo_file_path": "does-not-exist.png"}
    assert "DONINGTON VALE" in _text(_render(tmp_path, company)).upper()
