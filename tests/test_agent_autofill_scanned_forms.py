"""
Finding the blanks in a form that is a photograph of a form.

A scan has no vector graphics, so pdfplumber reports zero lines and zero
blanks, and a document a person can plainly see is a form comes back as "no
draft could be produced". These tests cover recovering the ruled grid from the
page image instead.

The OCR is supplied rather than called. Vision bills per page, and what is
under test here is this package's geometry — which runs of ink count as rules,
which cells count as empty, which label attaches to which blank — not whether
Google can read South African print.
"""

from __future__ import annotations

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF renders the page")
pytest.importorskip("scipy", reason="scipy.ndimage does the line detection")
pytest.importorskip("numpy")

from agent_autofill.extraction.ocr import OcrResult, OcrWord
from agent_autofill.extraction import scanned_form_extractor as sfe
from agent_autofill.extraction.scanned_form_extractor import (
    cells_from_rules,
    detect_rules,
    extract_scanned_blanks,
)

#: Geometry of the synthetic form, in points. Two label/answer rows, laid out
#: the way an SBD 1 is: label on the left, answer space to the right.
LEFT, MID, RIGHT = 60.0, 200.0, 520.0
ROW_TOPS = [300.0, 320.0, 340.0]        # two rows, 20pt each


def _ruled_page(doc, *, colour_block=False, text=True):
    """One page with a drawn table, optionally with a colour banner."""
    page = doc.new_page(width=595, height=842)
    for y in ROW_TOPS:
        page.draw_line(fitz.Point(LEFT, y), fitz.Point(RIGHT, y),
                       color=(0, 0, 0), width=0.8)
    for x in (LEFT, MID, RIGHT):
        page.draw_line(fitz.Point(x, ROW_TOPS[0]), fitz.Point(x, ROW_TOPS[-1]),
                       color=(0, 0, 0), width=0.8)
    if text:
        page.insert_text(fitz.Point(LEFT + 3, ROW_TOPS[0] + 14),
                         "NAME OF BIDDER", fontsize=9, fontname="helv")
        page.insert_text(fitz.Point(LEFT + 3, ROW_TOPS[1] + 14),
                         "POSTAL ADDRESS", fontsize=9, fontname="helv")
    if colour_block:
        # A saturated banner across the footer, like the one on every page of
        # the real tender. It is the reason ink must be grey as well as dark.
        page.draw_rect(fitz.Rect(0, 700, 595, 800), color=None,
                       fill=(0.95, 0.75, 0.1))
    return page


def _scanned(tmp_path, name="scan.pdf", **kwargs):
    """Render a drawn form to a bitmap and re-wrap it: a true scan."""
    source = fitz.open()
    page = _ruled_page(source, **kwargs)
    pixmap = page.get_pixmap(dpi=150)

    out = fitz.open()
    target = out.new_page(width=page.rect.width, height=page.rect.height)
    target.insert_image(target.rect, stream=pixmap.tobytes("png"))
    path = tmp_path / name
    out.save(str(path))
    out.close()
    source.close()
    return path


def _word(text, x0, top, x1=None, bottom=None, page=0):
    return OcrWord(text=text,
                   bbox=(x0, top, x1 if x1 is not None else x0 + 60,
                         bottom if bottom is not None else top + 9),
                   page_number=page, confidence=0.99)


def _ocr(words):
    return OcrResult(text=" ".join(w.text for w in words), words=list(words),
                     pages_read=1, pages_total=1, available=True)


LABELS = _ocr([
    _word("NAME", LEFT + 3, ROW_TOPS[0] + 6, LEFT + 40),
    _word("OF", LEFT + 44, ROW_TOPS[0] + 6, LEFT + 56),
    _word("BIDDER", LEFT + 60, ROW_TOPS[0] + 6, LEFT + 110),
    _word("POSTAL", LEFT + 3, ROW_TOPS[1] + 6, LEFT + 50),
    _word("ADDRESS", LEFT + 54, ROW_TOPS[1] + 6, LEFT + 120),
])


# --- finding the rules -----------------------------------------------------


def test_rules_are_recovered_from_the_page_image(tmp_path):
    doc = fitz.open(str(_scanned(tmp_path)))
    try:
        rules = detect_rules(doc[0])
    finally:
        doc.close()

    tops = sorted(r.position for r in rules.horizontal)
    assert len(tops) == 3, tops
    for found, drawn in zip(tops, ROW_TOPS):
        assert abs(found - drawn) <= 2.0, (found, drawn)

    lefts = sorted(r.position for r in rules.vertical)
    assert len(lefts) == 3, lefts
    for found, drawn in zip(lefts, (LEFT, MID, RIGHT)):
        assert abs(found - drawn) <= 2.0, (found, drawn)


def test_a_colour_banner_is_not_mistaken_for_rules(tmp_path):
    """
    Ink must be grey as well as dark.

    Thresholding on darkness alone reported 29 vertical rules on a page that
    has 11; the extra ones were the tender's footer banner, because a solid
    colour block is an enormous number of long thin runs to a morphological
    opening.
    """
    plain = fitz.open(str(_scanned(tmp_path, "plain.pdf")))
    banner = fitz.open(str(_scanned(tmp_path, "banner.pdf", colour_block=True)))
    try:
        without = detect_rules(plain[0])
        with_banner = detect_rules(banner[0])
    finally:
        plain.close()
        banner.close()

    assert len(with_banner.vertical) == len(without.vertical)
    assert len(with_banner.horizontal) == len(without.horizontal)


def test_text_is_not_mistaken_for_a_rule(tmp_path):
    """A rule is long AND thin; a word is neither."""
    doc = fitz.open(str(_scanned(tmp_path)))
    try:
        rules = detect_rules(doc[0])
    finally:
        doc.close()
    # The label text sits inside the first row band and must not appear as an
    # extra horizontal rule between the drawn ones.
    assert len(rules.horizontal) == 3


# --- rebuilding the grid ---------------------------------------------------


def test_cells_are_bounded_by_four_rules(tmp_path):
    doc = fitz.open(str(_scanned(tmp_path)))
    try:
        cells = cells_from_rules(detect_rules(doc[0]))
    finally:
        doc.close()
    # 2 bands x 2 columns
    assert len(cells) == 4, cells


def test_a_sliver_band_produces_no_cells():
    """
    The height floor, which exists because of a real defect.

    A stray rule slices a thin band off the bottom of every cell it crosses,
    and each sliver looks like an empty labelled cell. One of them wrote the
    tax compliance PIN into the "SUPPLIER COMPLIANCE STATUS" label box on a
    rendered page. Real fields measured 14.4-34.9pt; every sliver was 12.2pt.
    """
    rules = sfe.PageRules(
        horizontal=[sfe.RuleSegment(300.0, LEFT, RIGHT),
                    sfe.RuleSegment(312.0, LEFT, RIGHT)],   # 12pt apart
        vertical=[sfe.RuleSegment(LEFT, 300.0, 312.0),
                  sfe.RuleSegment(RIGHT, 300.0, 312.0)],
        width=595.0, height=842.0)
    assert cells_from_rules(rules) == []


def test_a_vertical_that_does_not_cross_a_band_does_not_split_it():
    """Columns change from row to row; a global grid invents cells."""
    rules = sfe.PageRules(
        horizontal=[sfe.RuleSegment(300.0, LEFT, RIGHT),
                    sfe.RuleSegment(320.0, LEFT, RIGHT)],
        vertical=[sfe.RuleSegment(LEFT, 300.0, 320.0),
                  sfe.RuleSegment(MID, 500.0, 520.0),   # elsewhere on the page
                  sfe.RuleSegment(RIGHT, 300.0, 320.0)],
        width=595.0, height=842.0)
    cells = cells_from_rules(rules)
    assert len(cells) == 1
    assert cells[0][0] == LEFT and cells[0][2] == RIGHT


# --- turning cells into blanks ---------------------------------------------


def test_empty_cells_become_blanks_labelled_from_the_left(tmp_path):
    blanks = extract_scanned_blanks(_scanned(tmp_path), ocr_result=LABELS)
    labels = {b.label_text for b in blanks}
    assert "NAME OF BIDDER" in labels
    assert "POSTAL ADDRESS" in labels
    assert all(b.label_origin == "cell_left" for b in blanks)


def test_a_cell_containing_text_is_not_a_blank(tmp_path):
    """The label cell is not somewhere to write an answer."""
    blanks = extract_scanned_blanks(_scanned(tmp_path), ocr_result=LABELS)
    for blank in blanks:
        assert blank.bbox[0] >= MID - 2.0, (
            f"{blank.label_text} starts at {blank.bbox[0]} — that is the label "
            f"column, which has text in it")


def test_labels_read_in_line_order_not_by_raw_top(tmp_path):
    """
    OCR gives each word its own top, so sorting on it scrambles a wrapped
    label. "CELL PHONE NUMBER" came back as "PHONE CELL NUMBER", which no
    alias in the dictionary matches.
    """
    words = _ocr([
        # Deliberately jittered tops, as OCR really returns them.
        _word("CELL", LEFT + 3, ROW_TOPS[0] + 6.0, LEFT + 34),
        _word("PHONE", LEFT + 60, ROW_TOPS[0] + 4.5, LEFT + 100),
        _word("NUMBER", LEFT + 3, ROW_TOPS[0] + 14.0, LEFT + 60),
    ])
    blanks = extract_scanned_blanks(_scanned(tmp_path), ocr_result=words)
    assert blanks
    assert blanks[0].label_text == "CELL PHONE NUMBER"


def test_split_cells_with_one_label_merge_into_one_blank():
    """
    A rule that divides a row visually does not always divide it as a field.

    "NAME OF BIDDER" is one answer space to the right margin; detection split
    it at an internal rule into two cells with the same label, and filling both
    writes the company name on one line twice.
    """
    from agent_autofill.extraction.layout_blank_extractor import Blank

    def blank(x0, x1):
        return Blank(source="pdf", page_number=0, bbox=(x0, 380.0, x1, 395.0),
                     label_text="NAME OF BIDDER", label_bbox=None,
                     confidence=0.62, strategy="scanned_rule",
                     label_origin="cell_left")

    merged = sfe._merge_split_cells([blank(143, 286), blank(286, 554)])
    assert len(merged) == 1
    assert merged[0].bbox == (143.0, 380.0, 554.0, 395.0)


def test_different_fields_sharing_a_label_are_not_merged():
    """The CODE / NUMBER pairs in the telephone rows are separate answers."""
    from agent_autofill.extraction.layout_blank_extractor import Blank

    def blank(x0, x1):
        return Blank(source="pdf", page_number=0, bbox=(x0, 424.0, x1, 448.0),
                     label_text="CODE", label_bbox=None, confidence=0.62,
                     strategy="scanned_rule", label_origin="cell_left")

    # A gap between them: not one field split, two fields.
    assert len(sfe._merge_split_cells([blank(226, 287), blank(386, 554)])) == 2


def test_page_numbers_are_zero_based_like_blank(tmp_path):
    """Reading this as 1-based wrote every value one page early once already."""
    blanks = extract_scanned_blanks(_scanned(tmp_path), ocr_result=LABELS)
    assert blanks
    assert all(b.page_number == 0 for b in blanks)


def test_confidence_is_lower_than_the_vector_path(tmp_path):
    """Every label here came through OCR and may be misread."""
    blanks = extract_scanned_blanks(_scanned(tmp_path), ocr_result=LABELS)
    assert blanks
    assert all(0.5 < b.confidence < 0.9 for b in blanks)


# --- failing quietly -------------------------------------------------------


def test_no_ocr_means_no_blanks_rather_than_an_exception(tmp_path):
    """A scan that cannot be read is the situation the caller was already in."""
    unavailable = OcrResult(available=False, reason="not configured")
    assert extract_scanned_blanks(_scanned(tmp_path),
                                  ocr_result=unavailable) == []


def test_a_page_ocr_never_read_is_skipped(tmp_path):
    """Without words there are no labels, so there is nothing to match."""
    empty = OcrResult(text="", words=[], pages_read=0, pages_total=1,
                      available=True)
    assert extract_scanned_blanks(_scanned(tmp_path), ocr_result=empty) == []
