"""
Reading a tender that is a photograph of a tender.

WHY THIS EXISTS
---------------
A large share of South African tender packs are scans: someone printed the
document, signed it, and put it back through a copier. There is no text layer
at all. Every reader in this package returns nothing on one of those files —
`pypdf.extract_text()` gives "", the classifier sees an empty document and
calls it "not a tender", and `pdfplumber` finds no ruled lines because there
are no vector graphics, only a bitmap.

The user's own words: two of their three real files could not be read at all.

WHAT IT DOES
------------
Renders each page to an image and sends it to Google Cloud Vision's
`document_text_detection`, which is built for dense documents rather than
photographs of street signs. Returns the text and the word boxes.

WHY CLOUD VISION AND NOT TESSERACT
----------------------------------
Tesseract is free and offline and would have been the obvious pick, but it
needs a system binary that the Functions gen2 Python buildpack cannot install.
It would have worked perfectly on the developer's machine and been silently
absent in production — which is precisely what happened with rapidfuzz,
python-docx and pdfplumber, where a lazy import turned a missing dependency
into a log line nobody read and the whole feature had never once run live.

WHAT THIS DOES NOT DO
---------------------
It does not make a scanned form *fillable*. Placing a value needs a blank with
coordinates, and finding blanks on a scan means detecting ruled lines in a
bitmap — image processing this package does not do. So a scan becomes readable
(classification, eligibility, and the label text a person can check) and may
still produce no draft. `ocr_note()` says that in as many words, because a
silent "0 fields found" reads as "this form has no fields".

COST
----
Vision bills per page-image. A 145-page bid pack is 145 units if read whole,
and the classifier only ever looks at the first ~1500 characters. So this is
capped at `MAX_OCR_PAGES` and the cap is deliberately low. Reading more pages
is a decision someone should make on purpose.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("agent_autofill.ocr")

#: A page with fewer than this many characters is treated as having no usable
#: text layer. Not zero: a scanned page often carries a stray header, a page
#: number stamped by the copier, or a few characters of OCR someone else ran.
TEXT_LAYER_MIN_CHARS = 40

#: How many pages to sample when deciding whether a document is a scan. A pack
#: whose first pages are a scanned cover and whose body has text is still worth
#: OCR-ing, but the decision does not need the whole file.
SCAN_PROBE_PAGES = 5

#: Cost guard: the maximum pages sent to Vision for one document. Override with
#: OCR_MAX_PAGES. Low on purpose — see the module docstring.
MAX_OCR_PAGES = int(os.environ.get("OCR_MAX_PAGES", "5"))

#: Render resolution. 200 is the low end of what OCR reads reliably on a form
#: with 8pt print; 300 roughly doubles the bytes for a small accuracy gain.
RENDER_DPI = 200


@dataclass
class OcrWord:
    """One recognised word, positioned in PDF points."""

    text: str
    #: (x0, top, x1, bottom) in PDF points, top-left origin — the same
    #: convention as `layout_blank_extractor.Blank.bbox`, so these can be
    #: compared with blanks without a second coordinate system in play.
    bbox: tuple[float, float, float, float]
    #: 0-based, matching `Blank.page_number`. That field was read as 1-based
    #: once already and wrote every value onto the wrong page.
    page_number: int
    confidence: float = 0.0


@dataclass
class OcrResult:
    text: str = ""
    words: list[OcrWord] = field(default_factory=list)
    pages_read: int = 0
    pages_total: int = 0
    #: False when OCR could not run at all — no library, no credentials, no
    #: API. Distinct from "ran and found nothing", which is `usable` False with
    #: `available` True. The caller must be able to tell a broken configuration
    #: from a genuinely blank page.
    available: bool = True
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.available and bool(self.text.strip())

    @property
    def truncated(self) -> bool:
        return self.pages_total > self.pages_read


def ocr_available() -> tuple[bool, str]:
    """
    Whether OCR can run here, and if not, what a person should do about it.

    Checked before rendering anything, so a misconfigured deployment costs one
    cheap import instead of a page render per document.
    """
    try:
        from google.cloud import vision  # noqa: F401
    except ImportError:
        return False, ("OCR needs the google-cloud-vision package, which is not "
                       "installed. Add it with: pip install google-cloud-vision")

    try:
        import google.auth

        google.auth.default()
    except Exception as exc:  # noqa: BLE001 - any auth failure is the same answer
        return False, (f"OCR could not authenticate to Google Cloud ({exc}). "
                       f"Run: gcloud auth application-default login")

    return True, ""


def page_has_text_layer(page) -> bool:
    """True when a fitz page carries enough real text to skip OCR."""
    try:
        return len((page.get_text() or "").strip()) >= TEXT_LAYER_MIN_CHARS
    except Exception:  # noqa: BLE001 - a damaged page is treated as unreadable
        return False


def needs_ocr(path: str | Path) -> bool:
    """
    True when this PDF has no usable text layer on the pages sampled.

    Deliberately conservative: a document with any real text on its first pages
    is left alone. OCR-ing a document that already has text is a cost with no
    benefit and a risk — the recognised text is a second, worse copy of what is
    already there.
    """
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        return False
    try:
        import fitz
    except ImportError:
        return False

    try:
        doc = fitz.open(str(path))
    except Exception as exc:  # noqa: BLE001
        log.warning("ocr: could not open %s: %s", path.name, exc)
        return False
    try:
        for i in range(min(doc.page_count, SCAN_PROBE_PAGES)):
            if page_has_text_layer(doc[i]):
                return False
        return doc.page_count > 0
    finally:
        doc.close()


def ocr_note(result: OcrResult, filename: str = "") -> str:
    """
    One sentence a person can act on. Used in narration and in the pack result.

    Never "0 fields found" on its own: on a scan that sentence is dangerously
    close to "this form has nothing to fill in".
    """
    name = filename or "This document"
    if not result.available:
        return f"{name} looks like a scan and OCR is not available. {result.reason}"
    if not result.usable:
        return (f"{name} looks like a scan and OCR found no readable text in it. "
                f"It may be a photograph, upside down, or too low-resolution.")
    extra = ""
    if result.truncated:
        extra = (f" Only the first {result.pages_read} of {result.pages_total} "
                 f"pages were read.")
    return (f"{name} is a scan with no text layer, so it was read with OCR."
            f"{extra} Recognised text can contain mistakes, and a scan usually "
            f"has no fillable blanks to place values into — expect to complete "
            f"this one by hand.")


def ocr_pdf(path: str | Path, max_pages: int | None = None) -> OcrResult:
    """
    Read a scanned PDF with Cloud Vision.

    Returns an `OcrResult` in every case, including failure. Nothing here
    raises: OCR is an enhancement to reading a document, and a document that
    cannot be OCR-ed must still flow through the pipeline as an unreadable one
    rather than taking the pack down.
    """
    path = Path(path)
    limit = MAX_OCR_PAGES if max_pages is None else max_pages

    ok, reason = ocr_available()
    if not ok:
        log.warning("ocr: unavailable for %s: %s", path.name, reason)
        return OcrResult(available=False, reason=reason)

    try:
        import fitz
        from google.cloud import vision
    except ImportError as exc:
        return OcrResult(available=False, reason=str(exc))

    try:
        doc = fitz.open(str(path))
    except Exception as exc:  # noqa: BLE001
        return OcrResult(available=False, reason=f"could not open the file: {exc}")

    texts: list[str] = []
    words: list[OcrWord] = []
    pages_total = doc.page_count
    pages_read = 0

    try:
        client = vision.ImageAnnotatorClient()
        for index in range(min(pages_total, limit)):
            page = doc[index]
            try:
                pixmap = page.get_pixmap(dpi=RENDER_DPI)
            except Exception as exc:  # noqa: BLE001
                log.warning("ocr: could not render page %d of %s: %s",
                            index + 1, path.name, exc)
                continue

            # Derived from the pixmap rather than assumed from RENDER_DPI:
            # PyMuPDF clamps very large renders, and a page whose size differs
            # from the request would put every box in the wrong place.
            scale_x = pixmap.width / page.rect.width if page.rect.width else 1.0
            scale_y = pixmap.height / page.rect.height if page.rect.height else 1.0

            response = client.document_text_detection(
                image=vision.Image(content=pixmap.tobytes("png"))
            )
            if response.error.message:
                log.warning("ocr: Vision refused page %d of %s: %s",
                            index + 1, path.name, response.error.message)
                continue

            pages_read += 1
            if response.full_text_annotation.text:
                texts.append(response.full_text_annotation.text)
            words.extend(_words_from(response, index, scale_x, scale_y))
    except Exception as exc:  # noqa: BLE001 - network, quota, permission
        log.warning("ocr: failed on %s: %s", path.name, exc)
        if pages_read == 0:
            return OcrResult(available=False, pages_total=pages_total,
                             reason=f"the OCR request failed: {exc}")
    finally:
        doc.close()

    log.info("ocr %s: %d/%d page(s), %d word(s)",
             path.name, pages_read, pages_total, len(words))
    return OcrResult(text="\n".join(texts), words=words,
                     pages_read=pages_read, pages_total=pages_total)


def _words_from(response, page_index: int, scale_x: float,
                scale_y: float) -> list[OcrWord]:
    """
    Flatten Vision's page/block/paragraph/word tree into positioned words.

    Vertices are pixels in the rendered image; dividing by the render scale puts
    them back into PDF points. Both systems have a top-left origin, so there is
    no flip here — and if that ever changes, every box lands mirrored down the
    page rather than subtly wrong, which is at least obvious.
    """
    out: list[OcrWord] = []
    for page in response.full_text_annotation.pages:
        for block in page.blocks:
            for paragraph in block.paragraphs:
                for word in paragraph.words:
                    text = "".join(symbol.text for symbol in word.symbols)
                    if not text.strip():
                        continue
                    xs = [v.x for v in word.bounding_box.vertices]
                    ys = [v.y for v in word.bounding_box.vertices]
                    if not xs or not ys:
                        continue
                    out.append(OcrWord(
                        text=text,
                        bbox=(min(xs) / scale_x, min(ys) / scale_y,
                              max(xs) / scale_x, max(ys) / scale_y),
                        page_number=page_index,
                        confidence=float(getattr(word, "confidence", 0.0) or 0.0),
                    ))
    return out
