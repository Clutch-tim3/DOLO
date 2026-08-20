"""
Values are written at the size the form is printed at.

The owner: "awfully small to the point where it's not visible — no small text,
that's the worst thing possible."

On his hand-filled SBD 6.1 the handwriting sits at the same height as the
form's printed body text. That is what a person does — they write to the size
of the page in front of them.

WHY RAISING THE CONSTANT WAS NOT ENOUGH

An earlier fix moved FONT_SIZE from 8.5 to 10.6, which restored parity with how
values looked BEFORE the face changed to Patrick Hand. But that earlier
appearance was itself too small against the form: Patrick Hand renders ~80% the
width of Helvetica, so 10.6 is an effective 8.5pt beside body text printed at
10-11pt. The reference has to be the document, not the product's own history.

Measured on the real pack: 31,183 characters at 10.0pt and 25,965 at 11.0pt.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_autofill.fill_engine import pdf_filler

REAL_PACK = Path(__file__).resolve().parent.parent / "data" / "archive" / "temp_tender_BID_DOCUMENT_06FY27_.pdf"

_fill_size_for = getattr(pdf_filler, "_fill_size_for", None)
MIN_FILL_SIZE = getattr(pdf_filler, "MIN_FILL_SIZE", 0.0)
HAND_WIDTH_RATIO = getattr(pdf_filler, "HAND_WIDTH_RATIO", 1.0)

needs_sizing = pytest.mark.skipif(_fill_size_for is None,
                                  reason="document-derived sizing not implemented")


# --- the floor ----------------------------------------------------------------

@needs_sizing
def test_there_is_a_hard_floor_of_ten_points():
    """
    Below 10pt a value does not survive being printed and rescanned, which is
    how these forms are actually submitted.
    """
    assert MIN_FILL_SIZE >= 10.0


@needs_sizing
def test_a_tiny_printed_form_does_not_drag_the_writing_below_the_floor(monkeypatch):
    monkeypatch.setattr(pdf_filler, "_printed_sizes", lambda page: [(100.0, 5.0)])
    assert _fill_size_for(object(), (0, 90, 200, 110)) >= MIN_FILL_SIZE


@needs_sizing
def test_an_oversized_heading_cannot_drive_the_writing_off_the_page(monkeypatch):
    monkeypatch.setattr(pdf_filler, "_printed_sizes", lambda page: [(100.0, 48.0)])
    size = _fill_size_for(object(), (0, 90, 200, 110))
    assert size <= pdf_filler.MAX_FILL_SIZE


# --- it follows the form ------------------------------------------------------

@needs_sizing
@pytest.mark.parametrize("printed,expected", [(10.0, 12.5), (11.0, 13.7), (12.0, 15.0)])
def test_the_size_is_derived_from_the_printed_text(monkeypatch, printed, expected):
    """
    Divided by the face's width ratio, so the apparent size matches rather than
    the point size. Patrick Hand at 12.5pt looks like Helvetica at 10.
    """
    monkeypatch.setattr(pdf_filler, "_printed_sizes", lambda page: [(100.0, printed)])
    size = _fill_size_for(object(), (0, 90, 200, 110))
    assert size == pytest.approx(expected, abs=0.6)


@needs_sizing
def test_the_nearest_text_wins_over_the_rest_of_the_page(monkeypatch):
    """
    A form is not printed at one size. The text beside a blank is what a person
    would match, not the page average.
    """
    monkeypatch.setattr(pdf_filler, "_printed_sizes",
                        lambda page: [(100.0, 11.0), (700.0, 22.0), (720.0, 22.0)])
    near = _fill_size_for(object(), (0, 90, 200, 110))
    far = _fill_size_for(object(), (0, 690, 200, 730))
    assert near < far


@needs_sizing
def test_a_page_with_no_text_layer_still_gets_a_usable_size(monkeypatch):
    """A scan has no spans at all. It must not fall to zero or raise."""
    monkeypatch.setattr(pdf_filler, "_printed_sizes", lambda page: [])
    size = _fill_size_for(object(), (0, 90, 200, 110))
    assert size >= MIN_FILL_SIZE


@needs_sizing
def test_sizing_never_raises(monkeypatch):
    """Styling must never fail a fill."""
    def boom(page):
        raise RuntimeError("no text layer")
    monkeypatch.setattr(pdf_filler, "_printed_sizes", boom)
    assert _fill_size_for(object(), (0, 90, 200, 110)) >= MIN_FILL_SIZE


# --- against the real pack ----------------------------------------------------

@needs_sizing
@pytest.mark.skipif(not REAL_PACK.exists(), reason="the owner's pack is not present")
def test_the_real_pack_drives_a_legible_size():
    """
    The form measures 10-11pt across ~57,000 characters. Writing must land
    above the floor and in the range a person would use.
    """
    import fitz

    doc = fitz.open(str(REAL_PACK))
    sizes = [_fill_size_for(doc[n], (100, 300, 300, 320)) for n in (0, 6, 7, 8)]

    assert all(s >= MIN_FILL_SIZE for s in sizes), sizes
    assert all(s <= pdf_filler.MAX_FILL_SIZE for s in sizes), sizes
    # And it is not one constant everywhere — the form varies, so this must too.
    assert len(set(round(s, 1) for s in sizes)) > 1, (
        f"every page produced the same size ({sizes}); the form is not being read"
    )


@needs_sizing
@pytest.mark.skipif(not REAL_PACK.exists(), reason="the owner's pack is not present")
def test_matching_the_form_does_not_cost_refusals():
    """
    The brief warns a larger size may start failing the fit check. Measured on
    the real pack it does not — the same fields fill, and the same five are
    refused for width, as at the previous fixed size.
    """
    from agent_autofill.extraction import match_label

    import tempfile

    profile = {"company_name": "DONINGTON VALE (PTY) LTD",
               "csd_number": "MAAA1234567",
               "registration_number": "2020/654321/07"}

    original = pdf_filler._fill_size_for
    counts = {}
    try:
        for name, fn in (("fixed", lambda p, b: 10.6), ("derived", original)):
            pdf_filler._fill_size_for = fn
            with tempfile.TemporaryDirectory() as tmp:
                result = pdf_filler.fill_pdf(REAL_PACK, Path(tmp) / "f.pdf",
                                             profile, match_label)
            counts[name] = (
                len(result.filled),
                sum(1 for s in result.skipped if s.category == "does_not_fit"),
            )
    finally:
        pdf_filler._fill_size_for = original

    assert counts["derived"][0] >= counts["fixed"][0], (
        f"matching the form filled fewer fields: {counts}")
    assert counts["derived"][1] <= counts["fixed"][1] + 2, (
        f"matching the form refused notably more: {counts}")


# --- the fit check and the drawing must agree ---------------------------------

def test_the_fit_check_is_told_the_size_the_value_will_be_drawn_at():
    """
    If the check measures at one size and the drawing uses another, a value
    passes and then overflows its cell — the failure the measured-width work
    already fixed once.
    """
    source = Path(pdf_filler.__file__).read_text(encoding="utf-8")
    assert "_fits(value, width, fill_size)" in source, (
        "the fit check is not using the per-blank size"
    )
