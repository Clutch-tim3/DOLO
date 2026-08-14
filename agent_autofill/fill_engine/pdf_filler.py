"""
Fill a PDF form by writing into the blanks, at their coordinates.

WHY THIS EXISTS
---------------
`document_filler` writes .docx. South African tender packs are overwhelmingly
PDF, and most are print-designed: no AcroForm, no interactive fields, just
ruled lines with a label beside them. A real pack submitted by a user came back
"no draft could be produced from any document" — the pipeline had read the
tender correctly at 99% confidence and then had nowhere to put anything.

So this writes text onto the page at the blank's own coordinates, which
`layout_blank_extractor` already finds.

WHAT IT DOES NOT DO
-------------------
Decide anything. Every fill-or-refuse decision comes from `safe_fill_fields.
decide` and `never_fill_fields.is_blocked`, the same two functions the .docx
path uses. There is no second rule set here and there must never be one: the
blocklist is the thing standing between a stored profile and a signed
declaration, and a copy of it would drift from the original the moment either
changed.

DRAWING ON SOMEONE ELSE'S FORM
------------------------------
This is a heavier act than filling a .docx, and the safeguards are stronger
because of it:

* The source is never touched. A copy is opened and written.
* Every value written is highlighted, so a reader can see at a glance what came
  from CairoAI rather than from a person.
* Every refusal is marked in place with the same `[ ! ]` used in .docx, so a
  blank that was deliberately left is visibly different from one that was
  missed.
* Text that will not fit its blank is not written at all. A value spilling over
  a neighbouring field is worse than an empty line, because it looks like an
  answer to the wrong question.
* Nothing is flattened. The result is a normal PDF a person can still edit.
"""

from __future__ import annotations

import logging
from pathlib import Path

from agent_autofill.extraction.layout_blank_extractor import extract_pdf_blanks
from agent_autofill.fill_engine.document_filler import (
    FilledField,
    FillResult,
    SkippedField,
)
from agent_autofill.fill_engine.never_fill_fields import is_blocked
from agent_autofill.fill_engine.safe_fill_fields import decide

log = logging.getLogger("agent_autofill.pdf_filler")

#: The highlight behind a written value. Matches the .docx path's gold shading
#: closely enough to read as the same product, in the RGB floats fitz wants.
FILL_HIGHLIGHT = (0.953, 0.914, 0.839)

#: Written where a field was deliberately not filled. Same marker as .docx, so
#: "look for [ ! ]" is one instruction across both formats.
SKIP_MARKER = "[ ! ]"

#: Point size for written values. Small enough to sit inside a typical ruled
#: line without colliding with the line above.
FONT_SIZE = 8.5
SKIP_FONT_SIZE = 7.5

#: A blank narrower than this cannot hold anything meaningful.
MIN_BLANK_WIDTH = 24.0

#: Rough average glyph width as a fraction of point size for Helvetica. Used to
#: refuse a value that would overflow rather than writing it and hoping.
CHAR_WIDTH_RATIO = 0.5


def _fits(text: str, width: float, size: float = FONT_SIZE) -> bool:
    return len(str(text)) * size * CHAR_WIDTH_RATIO <= max(width - 4.0, 0)


def fill_pdf(source, output, profile: dict, match_label_fn) -> FillResult:
    """
    Produce a filled COPY of a PDF form.

    Mirrors `fill_docx`'s signature and returns the same `FillResult`, so the
    review gate, the pack aggregation and the export path all work on a PDF
    draft without knowing it is one.
    """
    import fitz  # PyMuPDF. Imported here so a machine without it can still
                 # import the fill engine and use the .docx path.

    source = Path(source)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    filled: list[FilledField] = []
    skipped: list[SkippedField] = []

    blanks = extract_pdf_blanks(str(source))
    if not blanks:
        return FillResult(source_path=str(source), output_path=str(output),
                          filled=[], skipped=[], context=set())

    # Opened from the SOURCE and saved to the OUTPUT. PyMuPDF refuses a full
    # rewrite of a file it has open ("save to original must be incremental"),
    # and this ordering is better anyway: the user's original is never opened
    # for writing at all, so a crash mid-fill cannot damage it.
    doc = fitz.open(str(source))
    try:
        for blank in blanks:
            label = (getattr(blank, "label_text", "") or "").strip()
            # `Blank.page_number` is 0-BASED — the extractor says so in a
            # comment on the field, and this code read it as 1-based. Every
            # value was written one page early, into whatever happened to be
            # there. On the real 145-page tender that put seven fields onto a
            # nearly empty page while the form they belonged to stayed blank,
            # and the counts still said "31 filled" because the data was right
            # and only the placement was wrong. Nothing but rendering a page
            # and looking at it would have caught this.
            page_index = getattr(blank, "page_number", 0) or 0
            bbox = getattr(blank, "bbox", None)
            location = f"page {page_index + 1}"   # 1-based for a human

            if bbox is None or page_index < 0 or page_index >= doc.page_count:
                skipped.append(SkippedField(
                    label=label or "(unlabelled)",
                    reason="This blank has no position on the page, so nothing "
                           "could be written into it safely.",
                    category="unplaceable", location=location))
                continue

            page = doc[page_index]
            x0, y0, x1, y1 = bbox
            width = float(x1) - float(x0)

            # --- the decision. Not made here. ---------------------------------
            block = is_blocked(label, section=getattr(blank, "notes", None) or None)
            if block.blocked:
                _mark_skipped(page, bbox)
                skipped.append(SkippedField(
                    label=label or "(unlabelled)",
                    reason=block.message, category="blocked", location=location))
                continue

            match = match_label_fn(label) if label else None
            canonical = getattr(match, "canonical", None) if match else None
            score = getattr(match, "score", 0.0) if match else 0.0
            if not canonical:
                _mark_skipped(page, bbox)
                skipped.append(SkippedField(
                    label=label or "(unlabelled)",
                    reason="I could not tell what this field is asking for.",
                    category="unmatched", location=location))
                continue

            verdict = decide(canonical, label, profile, match_score=score)
            if not verdict.fill:
                _mark_skipped(page, bbox)
                skipped.append(SkippedField(
                    label=label, reason=verdict.reason,
                    category=("blocked" if verdict.block else "no_data"),
                    location=location))
                continue

            value = str(verdict.value)
            if width < MIN_BLANK_WIDTH or not _fits(value, width):
                # Refused rather than truncated. A half-written registration
                # number is worse than an empty line: it looks like an answer.
                _mark_skipped(page, bbox)
                skipped.append(SkippedField(
                    label=label,
                    reason=("The value does not fit this blank, so it was left "
                            "for you rather than written across the field next "
                            "to it."),
                    category="does_not_fit", location=location))
                continue

            _write_value(page, bbox, value)
            filled.append(FilledField(
                label=label, value=value, source="company_profile",
                low_confidence=bool(getattr(verdict, "low_confidence", False)),
                location=location))

        # Full rewrite rather than incremental: the copy is ours to rewrite,
        # and an incremental save appends a revision that some readers show
        # with the original values still visible underneath.
        doc.save(str(output), incremental=False, deflate=True)
    finally:
        doc.close()

    log.info("pdf fill %s -> filled=%d skipped=%d",
             source.name, len(filled), len(skipped))
    return FillResult(source_path=str(source), output_path=str(output),
                      filled=filled, skipped=skipped, context=set())


def _write_value(page, bbox, value: str) -> None:
    """Highlight the blank, then write the value inside it."""
    import fitz

    x0, y0, x1, y1 = [float(v) for v in bbox]
    page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=None,
                   fill=FILL_HIGHLIGHT, overlay=False)
    # Baseline nudged up from the bottom edge so the text sits on the rule
    # rather than under it.
    page.insert_text(fitz.Point(x0 + 2, y1 - 2.5), value,
                     fontsize=FONT_SIZE, fontname="helv", color=(0, 0, 0))


def _mark_skipped(page, bbox) -> None:
    """
    Put `[ ! ]` where a field was deliberately left. A blank that was refused
    on purpose must not look like one that was missed.
    """
    import fitz

    x0, y0, x1, y1 = [float(v) for v in bbox]
    if (x1 - x0) < 14.0:
        return
    page.insert_text(fitz.Point(x0 + 2, y1 - 2.5), SKIP_MARKER,
                     fontsize=SKIP_FONT_SIZE, fontname="helv",
                     color=(0.72, 0.11, 0.11))
