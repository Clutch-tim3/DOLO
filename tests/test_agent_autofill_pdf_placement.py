"""
Where `fill_pdf` puts the text.

WHY THIS FILE EXISTS
--------------------
`Blank.page_number` is 0-based — the dataclass says so in a comment on the
field — and `pdf_filler` read it as 1-based, so it wrote every value to
`doc[page_number - 1]`: one page earlier than the field it belonged to.

Nothing caught it. The counts were right (31 fields filled), the values were
right (the correct profile value for the correct label), the review list was
right, the export gate was right. Only the *position* was wrong, and no
assertion in the suite looked at a position. On the real 145-page tender it put
seven values into the empty lower half of page 6 while the SBD 1 form on page 7
stayed blank. It took rendering a page to a PNG and looking at it.

So these tests assert the thing the counts cannot: that a value lands on the
page its blank named, and that the page before it is untouched.

The blanks are constructed directly rather than extracted, on purpose. The bug
was in the filler's reading of the contract, so the test states the contract
literally: page_number=1 means the SECOND page. Driving this through the real
extractor would test the extractor's heuristics too, and a change in those
would move this test's ground truth — which is how a regression test quietly
stops testing its regression.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF is required for PDF filling")

from agent_autofill.extraction.layout_blank_extractor import Blank
from agent_autofill.fill_engine import pdf_filler
from agent_autofill.fill_engine.pdf_filler import fill_pdf

PROFILE = {"company_name": "CairoAI (Pty) Ltd"}

#: Roomy enough to clear MIN_BLANK_WIDTH and the _fits() overflow check, so a
#: refusal in these tests means a placement bug and not a sizing one.
WIDE_BBOX = (60.0, 300.0, 460.0, 316.0)


def _blank(page_number: int, bbox=WIDE_BBOX, label: str = "NAME OF BIDDER") -> Blank:
    return Blank(
        source="pdf",
        page_number=page_number,
        bbox=bbox,
        label_text=label,
        label_bbox=None,
        confidence=0.9,
        strategy="ruled_cell",
        label_origin="cell_left",
    )


def _match_company_name(label: str):
    """Stand-in for `field_alias_dictionary.match_label`.

    Returns the canonical the profile has a value for, so `decide()` fills.

    The score is on rapidfuzz's 0-100 scale, which is what `decide()` compares
    against its 88 threshold. Passing 0.95 here reads as 0.95% and is refused
    — a trap worth naming, since a 0-1 score is the more natural guess.
    """
    return SimpleNamespace(canonical="company_name", score=95.0)


def _three_page_pdf(path) -> None:
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=595, height=842)   # A4
    doc.save(str(path))
    doc.close()


def _page_text(path, index: int) -> str:
    doc = fitz.open(str(path))
    try:
        return doc[index].get_text()
    finally:
        doc.close()


@pytest.fixture()
def blank_pdf(tmp_path):
    src = tmp_path / "form.pdf"
    _three_page_pdf(src)
    return src


def test_value_lands_on_the_page_the_blank_named(blank_pdf, tmp_path, monkeypatch):
    """page_number=1 is the SECOND page. This is the regression."""
    out = tmp_path / "filled.pdf"
    monkeypatch.setattr(pdf_filler, "extract_pdf_blanks",
                        lambda _p: [_blank(page_number=1)])

    result = fill_pdf(blank_pdf, out, PROFILE, _match_company_name)

    assert len(result.filled) == 1, result.skipped
    assert "CairoAI (Pty) Ltd" in _page_text(out, 1)


def test_the_page_before_is_left_alone(blank_pdf, tmp_path, monkeypatch):
    """The old bug's signature: the value appearing one page early.

    Asserted separately from the line above because both were true of the
    broken code in the only case anyone checked — a single-page document, where
    off-by-one and correct are indistinguishable.
    """
    out = tmp_path / "filled.pdf"
    monkeypatch.setattr(pdf_filler, "extract_pdf_blanks",
                        lambda _p: [_blank(page_number=1)])

    fill_pdf(blank_pdf, out, PROFILE, _match_company_name)

    assert "CairoAI" not in _page_text(out, 0)
    assert "CairoAI" not in _page_text(out, 2)


def test_first_page_blank_uses_index_zero(blank_pdf, tmp_path, monkeypatch):
    """page_number=0 is the FIRST page, not a falsy value to be defaulted.

    `getattr(blank, "page_number", 0) or 0` is a correct no-op for 0 today, but
    the same idiom written as `or 1` is exactly how the original bug reappears.
    """
    out = tmp_path / "filled.pdf"
    monkeypatch.setattr(pdf_filler, "extract_pdf_blanks",
                        lambda _p: [_blank(page_number=0)])

    result = fill_pdf(blank_pdf, out, PROFILE, _match_company_name)

    assert len(result.filled) == 1, result.skipped
    assert "CairoAI (Pty) Ltd" in _page_text(out, 0)


def test_reported_location_is_one_based_for_the_reader(blank_pdf, tmp_path,
                                                       monkeypatch):
    """The human-facing string counts from 1, because documents do.

    This is the one place the two conventions are deliberately different, so it
    is worth pinning: a review list saying "page 0" is a bug report waiting to
    happen.
    """
    out = tmp_path / "filled.pdf"
    monkeypatch.setattr(pdf_filler, "extract_pdf_blanks",
                        lambda _p: [_blank(page_number=6)])

    result = fill_pdf(blank_pdf, out, PROFILE, _match_company_name)

    # Page 7 does not exist in a 3-page document, so this is refused rather
    # than written — and the refusal still names the page the way a person
    # would say it.
    assert not result.filled
    assert result.skipped[0].location == "page 7"


def test_page_out_of_range_is_refused_not_clamped(blank_pdf, tmp_path,
                                                  monkeypatch):
    """A blank pointing past the end must not fall back to the last page.

    Writing it somewhere is worse than not writing it: the value would look
    like an answer to whatever question happened to be there.
    """
    out = tmp_path / "filled.pdf"
    monkeypatch.setattr(pdf_filler, "extract_pdf_blanks",
                        lambda _p: [_blank(page_number=99)])

    result = fill_pdf(blank_pdf, out, PROFILE, _match_company_name)

    assert not result.filled
    assert result.skipped[0].category == "unplaceable"
    for i in range(3):
        assert "CairoAI" not in _page_text(out, i)


def test_source_pdf_is_never_modified(blank_pdf, tmp_path, monkeypatch):
    """The user's original is opened read-only and must stay byte-identical."""
    out = tmp_path / "filled.pdf"
    before = blank_pdf.read_bytes()
    monkeypatch.setattr(pdf_filler, "extract_pdf_blanks",
                        lambda _p: [_blank(page_number=1)])

    fill_pdf(blank_pdf, out, PROFILE, _match_company_name)

    assert blank_pdf.read_bytes() == before


# ---------------------------------------------------------------------------
# What gets marked, and how wide a value is allowed to be.
#
# Both of these were visible on a rendered page and invisible in the counts,
# same as the page indexing above: a value crossing into the next column still
# counts as "filled", and a red marker on a table header still counts as
# "skipped".
# ---------------------------------------------------------------------------

from agent_autofill.fill_engine.pdf_filler import (   # noqa: E402
    CHAR_WIDTH_RATIO,
    FIT_PADDING,
    FONT_SIZE,
    SKIP_MARKER,
    _fits,
    _text_width,
)

#: Narrow enough to clear MIN_BLANK_WIDTH but too small for the value, so a
#: refusal here is about width and not about the blank being unusable.
TIGHT_BBOX = (60.0, 300.0, 96.0, 316.0)


def test_the_fit_check_uses_the_measured_width():
    value = "TCS0001234567"
    real = _text_width(value)
    assert _fits(value, real + FIT_PADDING)
    assert not _fits(value, real + FIT_PADDING - 1.0)


def test_width_is_measured_in_the_face_the_value_is_drawn_in():
    """Measure the font you draw in, or the fit check means nothing.

    This used to assert the measured width EXCEEDS the average-glyph rule,
    which was a fact about Helvetica: on the real SBD 1 the Tax Compliance PIN
    measured 64.3pt against the rule's 55.25pt, and a value nine points too
    wide ran through the closing rule of its cell.

    Values are drawn in Patrick Hand now, which is NARROWER than Helvetica —
    so the direction of that error flipped, and pinning it would have pinned
    the wrong thing. What actually matters is that the measurement follows the
    face: measuring in Helvetica while drawing in handwriting would refuse
    values that fit perfectly well, and the reverse would overflow cells again.
    """
    from agent_autofill.fill_engine.pdf_filler import _handwriting

    value = "TCS0001234567"
    measured = _text_width(value)
    estimate = len(value) * FONT_SIZE * CHAR_WIDTH_RATIO

    # A real measurement, not the crude average.
    assert abs(measured - estimate) > 1.0, (
        f"{measured} looks like the average rule ({estimate}), not a measurement")

    face = _handwriting()
    if face is not None:
        assert measured == pytest.approx(
            face.text_length(value, fontsize=FONT_SIZE), abs=0.01), (
            "width was measured in a different font from the one drawn")

    # And the fit check follows the measurement, whichever way it goes.
    assert _fits(value, measured + FIT_PADDING)
    assert not _fits(value, measured + FIT_PADDING - 1.0)


def test_a_value_too_wide_for_its_blank_is_refused_not_truncated(
        blank_pdf, tmp_path, monkeypatch):
    out = tmp_path / "filled.pdf"
    monkeypatch.setattr(pdf_filler, "extract_pdf_blanks",
                        lambda _p: [_blank(page_number=0, bbox=TIGHT_BBOX)])

    result = fill_pdf(blank_pdf, out, PROFILE, _match_company_name)

    assert not result.filled
    assert result.skipped[0].category == "does_not_fit"
    # Not a shortened version of itself either — a clipped registration number
    # reads as an answer.
    assert "CairoAI" not in _page_text(out, 0)


def test_an_unmatched_cell_is_not_marked_on_the_page(blank_pdf, tmp_path,
                                                     monkeypatch):
    """`[ ! ]` means "refused on purpose", and a header is not a refusal.

    The ruled-cell extractor offered up a table header and an italic footnote
    as blanks, and both came back stamped in red on the user's own document.
    """
    out = tmp_path / "filled.pdf"
    monkeypatch.setattr(pdf_filler, "extract_pdf_blanks",
                        lambda _p: [_blank(page_number=0,
                                           label="BIDDING PROCEDURE ENQUIRIES")])

    result = fill_pdf(blank_pdf, out, PROFILE, lambda _l: None)

    assert result.skipped[0].category == "unmatched"
    assert SKIP_MARKER not in _page_text(out, 0)


def test_a_deliberate_refusal_is_still_marked(blank_pdf, tmp_path, monkeypatch):
    """The other half: dropping the marker everywhere would hide real refusals.

    A signature block left blank on purpose has to look different from one
    nobody got to.
    """
    out = tmp_path / "filled.pdf"
    monkeypatch.setattr(pdf_filler, "extract_pdf_blanks",
                        lambda _p: [_blank(page_number=0, label="SIGNATURE OF BIDDER")])

    result = fill_pdf(blank_pdf, out, PROFILE, _match_company_name)

    assert result.skipped[0].category == "blocked"
    assert SKIP_MARKER in _page_text(out, 0)


def test_the_highlight_is_visible_on_a_scanned_page(tmp_path, monkeypatch):
    """The band must be drawn OVER the page, not under it.

    `overlay=False` draws beneath existing content. On a vector PDF that is
    fine. On a scan the whole page is one image, so the highlight went behind
    the bitmap and no reader ever saw it — sampling a pixel inside a filled
    cell came back pure white.

    That safeguard matters most on exactly those documents: values are drawn in
    a handwriting face, and the band is the only thing left saying a machine
    put them there rather than a person.
    """
    source = tmp_path / "scan.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # A full-page image, the way a scanned document is built.
    white = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 800, 1100), False)
    white.clear_with(255)
    page.insert_image(page.rect, pixmap=white)
    doc.save(str(source))
    doc.close()

    out = tmp_path / "filled.pdf"
    monkeypatch.setattr(pdf_filler, "extract_pdf_blanks",
                        lambda _p: [_blank(page_number=0)])
    fill_pdf(source, out, PROFILE, _match_company_name)

    filled = fitz.open(str(out))
    try:
        rendered = filled[0].get_pixmap(dpi=150)
        scale = rendered.width / filled[0].rect.width
        # Inside the blank, clear of the glyphs themselves.
        pixel = rendered.pixel(int(430 * scale), int(305 * scale))[:3]
    finally:
        filled.close()

    assert pixel != (255, 255, 255), (
        "the highlight is not visible over the scanned page — it was drawn "
        "underneath the bitmap")
