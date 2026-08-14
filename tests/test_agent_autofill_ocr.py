"""
Reading a tender that has no text layer.

Two of the user's three real files were scans, and every reader in the package
returned nothing on them: pypdf gave "", the classifier called an empty
document "not a tender", and pdfplumber found no ruled lines because a scan has
no vector graphics.

The Vision client is stubbed here. These tests are about this package's code —
when it decides to spend money, how it converts pixel boxes back into PDF
points, and what it tells a user when OCR cannot run — not about whether
Google's OCR reads South African English.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF is required to render pages")

from agent_autofill.extraction import ocr as ocr_module
from agent_autofill.extraction.ocr import (
    OcrResult,
    needs_ocr,
    ocr_available,
    ocr_note,
    ocr_pdf,
)

TEXT = "NAME OF BIDDER  POSTAL ADDRESS  VAT REGISTRATION NUMBER  TAX COMPLIANCE PIN"


def _text_pdf(path, pages: int = 1):
    """An ordinary PDF with a real text layer."""
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text(fitz.Point(60, 300), TEXT, fontsize=11, fontname="helv")
    doc.save(str(path))
    doc.close()
    return path


def _scanned_pdf(path, pages: int = 1):
    """
    An image-only PDF — what a copier produces.

    Built by rendering a text page and pasting it back as a bitmap, so it is a
    genuine scan-shaped file rather than an empty one: it *looks* like text to
    a human and carries zero characters for a machine.
    """
    source = fitz.open()
    page = source.new_page(width=595, height=842)
    page.insert_text(fitz.Point(60, 300), TEXT, fontsize=11, fontname="helv")
    pixmap = page.get_pixmap(dpi=150)

    out = fitz.open()
    for _ in range(pages):
        target = out.new_page(width=595, height=842)
        target.insert_image(target.rect, stream=pixmap.tobytes("png"))
    out.save(str(path))
    out.close()
    source.close()
    return path


# --- a stub Vision client --------------------------------------------------


def _fake_word(text: str, x0: int, y0: int, x1: int, y1: int, confidence=0.99):
    vertices = [SimpleNamespace(x=x0, y=y0), SimpleNamespace(x=x1, y=y0),
                SimpleNamespace(x=x1, y=y1), SimpleNamespace(x=x0, y=y1)]
    return SimpleNamespace(
        symbols=[SimpleNamespace(text=c) for c in text],
        bounding_box=SimpleNamespace(vertices=vertices),
        confidence=confidence,
    )


def _fake_response(words, text="RECOGNISED TEXT"):
    page = SimpleNamespace(blocks=[SimpleNamespace(
        paragraphs=[SimpleNamespace(words=words)])])
    return SimpleNamespace(
        error=SimpleNamespace(message=""),
        full_text_annotation=SimpleNamespace(text=text, pages=[page]),
    )


class _FakeClient:
    """Records every page it was asked to read, so cost can be asserted."""

    calls = 0

    def __init__(self, *a, **k):
        type(self).calls = 0

    def document_text_detection(self, image=None):
        type(self).calls += 1
        # One word occupying a known pixel rectangle, so the scale conversion
        # has a right answer rather than a plausible one.
        return _fake_response([_fake_word("BIDDER", 278, 278, 556, 556)])


@pytest.fixture()
def vision_ok(monkeypatch):
    """Make OCR appear configured, with a client that costs nothing."""
    vision = pytest.importorskip("google.cloud.vision")
    import google.auth

    monkeypatch.setattr(google.auth, "default", lambda *a, **k: (None, "test-project"))
    monkeypatch.setattr(vision, "ImageAnnotatorClient", _FakeClient)
    return _FakeClient


# --- when to spend money ---------------------------------------------------


def test_a_scan_is_detected(tmp_path):
    assert needs_ocr(_scanned_pdf(tmp_path / "scan.pdf")) is True


def test_a_pdf_with_text_is_never_sent_to_ocr(tmp_path):
    """The cost guard. OCR-ing a readable PDF buys a worse copy of its text."""
    assert needs_ocr(_text_pdf(tmp_path / "real.pdf")) is False


def test_a_docx_is_never_sent_to_ocr(tmp_path):
    (tmp_path / "form.docx").write_bytes(b"PK\x03\x04 not really a docx")
    assert needs_ocr(tmp_path / "form.docx") is False


def test_read_head_does_not_call_ocr_when_the_page_has_text(tmp_path, monkeypatch):
    from agent_autofill.classification import is_tender_document as cls

    def explode(*a, **k):
        raise AssertionError("OCR was called on a document that already had text")

    monkeypatch.setattr(ocr_module, "ocr_pdf", explode)
    head = cls.read_head(_text_pdf(tmp_path / "real.pdf"))
    assert head.ocr_used is False
    assert "BIDDER" in head.text


# --- the conversion this package is responsible for ------------------------


def test_recognised_words_come_back_in_pdf_points(tmp_path, vision_ok):
    """
    Vision returns pixels in the rendered image; blanks are in PDF points.

    At 200 dpi the scale is 200/72, so a word spanning pixels 278..556 lands at
    roughly 100..200 points. Asserted with a tolerance because PyMuPDF may
    clamp a render — which is exactly why the scale is derived from the pixmap
    rather than assumed from the requested dpi.
    """
    result = ocr_pdf(_scanned_pdf(tmp_path / "scan.pdf"))
    assert result.available and result.usable
    word = result.words[0]
    assert word.text == "BIDDER"
    x0, top, x1, bottom = word.bbox
    assert x0 == pytest.approx(100, abs=3), word.bbox
    assert top == pytest.approx(100, abs=3), word.bbox
    assert x1 == pytest.approx(200, abs=3), word.bbox
    assert bottom == pytest.approx(200, abs=3), word.bbox


def test_word_page_numbers_are_zero_based_like_blank(tmp_path, vision_ok):
    """
    `OcrWord.page_number` must match `Blank.page_number`'s convention.

    Reading that field as 1-based is not hypothetical: it is what `pdf_filler`
    did, and it wrote every value onto the wrong page while every count stayed
    correct.
    """
    result = ocr_pdf(_scanned_pdf(tmp_path / "scan.pdf", pages=3))
    assert min(w.page_number for w in result.words) == 0
    assert max(w.page_number for w in result.words) == 2


def test_page_cap_limits_what_is_billed(tmp_path, vision_ok, monkeypatch):
    """Vision bills per page. A 145-page pack must not become 145 units."""
    monkeypatch.setattr(ocr_module, "MAX_OCR_PAGES", 2)
    result = ocr_pdf(_scanned_pdf(tmp_path / "scan.pdf", pages=6))
    assert vision_ok.calls == 2
    assert result.pages_read == 2
    assert result.pages_total == 6
    assert result.truncated is True


def test_the_cap_is_reported_so_a_partial_read_is_not_mistaken_for_the_whole(
        tmp_path, vision_ok, monkeypatch):
    monkeypatch.setattr(ocr_module, "MAX_OCR_PAGES", 1)
    result = ocr_pdf(_scanned_pdf(tmp_path / "scan.pdf", pages=4))
    assert "first 1 of 4 pages" in ocr_note(result, "scan.pdf")


# --- failing honestly ------------------------------------------------------


def test_missing_credentials_are_reported_with_the_fix(monkeypatch):
    import google.auth

    def no_creds(*a, **k):
        raise RuntimeError("Your default credentials were not found")

    monkeypatch.setattr(google.auth, "default", no_creds)
    ok, reason = ocr_available()
    assert ok is False
    assert "gcloud auth application-default login" in reason


def test_ocr_failure_is_available_false_not_an_empty_string(tmp_path, monkeypatch):
    """
    A broken configuration and a blank page must not look the same.

    The old behaviour returned "" for both, so "OCR is not set up" and "this
    page really is empty" produced identical output and the first was
    indistinguishable from the second.
    """
    import google.auth

    monkeypatch.setattr(google.auth, "default",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    result = ocr_pdf(_scanned_pdf(tmp_path / "scan.pdf"))
    assert result.available is False
    assert result.usable is False
    assert result.reason


def test_a_vision_error_on_a_page_does_not_raise(tmp_path, vision_ok, monkeypatch):
    """A pack must not be taken down by one unreadable page."""
    class Refusing(_FakeClient):
        def document_text_detection(self, image=None):
            return SimpleNamespace(
                error=SimpleNamespace(message="quota exceeded"),
                full_text_annotation=SimpleNamespace(text="", pages=[]))

    vision = pytest.importorskip("google.cloud.vision")
    monkeypatch.setattr(vision, "ImageAnnotatorClient", Refusing)
    result = ocr_pdf(_scanned_pdf(tmp_path / "scan.pdf"))
    assert result.pages_read == 0
    assert result.usable is False


# --- what the user is told -------------------------------------------------


def test_the_note_says_a_scan_may_still_not_be_fillable(tmp_path, vision_ok):
    """
    Honesty constraint. OCR makes a scan readable; it does not make it
    fillable, because placing a value needs a blank with coordinates and
    finding those on a bitmap is image processing this package does not do.
    A note that stopped at "read with OCR" would imply a draft is coming.
    """
    note = ocr_note(ocr_pdf(_scanned_pdf(tmp_path / "scan.pdf")), "scan.pdf")
    assert "by hand" in note
    assert "mistakes" in note


def test_the_note_never_reads_as_the_form_being_empty():
    """"0 fields found" on a scan reads as "this form has nothing to fill in"."""
    note = ocr_note(OcrResult(available=False, reason="not configured"), "scan.pdf")
    assert "scan" in note.lower()
    assert "not available" in note.lower()


def test_a_scan_now_gets_an_actionable_reason_not_cannot_read_it(tmp_path,
                                                                 monkeypatch):
    """
    The old message was "Agent Autofill cannot read it", which is a dead end.
    When the only problem is an unconfigured API, say that instead.
    """
    from agent_autofill.classification import is_tender_document as cls
    import google.auth

    monkeypatch.setattr(google.auth, "default",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    result = cls.classify_document(_scanned_pdf(tmp_path / "scan.pdf"), "co")
    assert result.status == "unreadable"
    assert result.ocr_used is True
    assert "cannot read it" not in result.reason
    assert "OCR is not available" in result.reason
