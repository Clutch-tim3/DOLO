"""
Find the blanks in a form that is a photograph of a form.

WHY THIS EXISTS
---------------
`layout_blank_extractor` finds fillable cells by reading the PDF's vector
graphics — the actual line objects the author drew. A scan has none of that. It
is one bitmap per page, so pdfplumber reports zero lines, zero rects and zero
blanks, and Agent Autofill reports "no draft could be produced" for a document
a person can plainly see is a form.

OCR gave us the words on a scanned page. This gives us the boxes to put values
into: the ruled lines are recovered from the image, the grid is rebuilt from
them, and every empty cell becomes a `Blank` — the same dataclass the vector
path produces, so the fill engine, the blocklist, the review gate and the
export path all work on a scanned form without knowing it is one.

HOW THE LINES ARE FOUND
-----------------------
A rule is ink that is LONG in one direction and THIN in the other. That single
observation does most of the work:

  1. Render the page and mark ink.
  2. Morphological opening with a long, one-pixel-tall element keeps only runs
     that survive being slid along horizontally — text strokes do not.
  3. Discard anything thicker than `MAX_RULE_THICKNESS_PT`. This is what
     separates a table rule from a bold heading or a filled-in block.
  4. Cluster positions within `CLUSTER_TOLERANCE_PT`, because a scanned line
     three pixels tall is three "lines" without it.

INK IS DARK **AND** GREY, WHICH MATTERS MORE THAN IT SOUNDS
-----------------------------------------------------------
Thresholding on darkness alone found 29 vertical rules on a page that has 11.
The extra ones were the colour banner across the footer of every page of the
tender: a solid image is, to a morphological opening, an enormous number of
long thin runs.

Forms are printed black on white. Requiring ink to be *desaturated* as well as
dark removes photographs, logos and colour blocks in one condition, and took
that page from 29 vertical rules to 10 — against a true 11, each within a
point.

WHAT IT DOES NOT DO
-------------------
Decide anything. It produces `Blank` objects and nothing else. Whether a value
may be written into one is `never_fill_fields.is_blocked` and
`safe_fill_fields.decide`, exactly as for a vector PDF. A scanned signature
block is refused by the same rule that refuses a vector one, and it must stay
that way: a second rule set would drift from the first the moment either
changed.

ACCURACY IS NOT PERFECT AND THE CALLER MUST TREAT IT THAT WAY
--------------------------------------------------------------
The labels come from OCR, so they carry OCR's mistakes. `Blank.confidence` is
set lower than the vector path's for exactly this reason, which feeds the
existing `is_actionable` threshold and pushes marginal fields to a human rather
than into a draft.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path

from agent_autofill.extraction.layout_blank_extractor import Blank

log = logging.getLogger("agent_autofill.scanned_form")

#: Matches `ocr.RENDER_DPI` so a page rendered for OCR and a page rendered for
#: line detection share one coordinate scale.
RENDER_DPI = 200

#: A rule must run at least this far to count. Shorter than the narrowest cell
#: of an SBD 1 and long enough to reject the stroke of any character.
MIN_RULE_LENGTH_PT = 40.0

#: And be no thicker than this. The separator between "a table rule" and "a
#: bold word" or "a filled black box".
MAX_RULE_THICKNESS_PT = 2.5

#: Positions closer together than this are the same rule seen twice. A scanned
#: line is several pixels thick and its edges land on different rows.
CLUSTER_TOLERANCE_PT = 3.0

#: Ink is darker than this (0 = black, 255 = white).
INK_MAX_GREY = 160

#: ...and less colourful than this (max channel - min channel). See the module
#: docstring: this one condition removes the footer banner, logos and any
#: photograph, and it is the difference between 29 detected rules and 10.
INK_MAX_SATURATION = 40

#: A cell smaller than this in either direction cannot hold a written value.
MIN_CELL_WIDTH_PT = 28.0

#: Height floor, and it earns its keep. A stray rule detected across the page
#: slices a thin band off the bottom of every cell it crosses, and each of
#: those slivers looks like an empty labelled cell — on the test page it
#: produced four, one of which wrote the tax compliance PIN into the
#: "SUPPLIER COMPLIANCE STATUS" label box on the rendered output.
#:
#: The separation is clean on real forms: genuine answer fields there measured
#: 14.4pt to 34.9pt, and every sliver measured 12.2pt. A cell this short cannot
#: hold 8.5pt text with any padding anyway, so nothing fillable is lost.
#:
#: Tuned against one tender pack. If scanned forms with genuinely compact
#: fields show up, this is the number to revisit — not the rule detection.
MIN_CELL_HEIGHT_PT = 13.0

#: ...and one larger than this is a page region, not a field.
MAX_CELL_HEIGHT_PT = 90.0

#: Below the vector path's 0.9, because every label here came through OCR and
#: may be misread. `DetectedField.is_actionable` requires >= 0.55, so this
#: still allows a confident alias match through while leaving room for the
#: threshold to be raised if scanned drafts prove unreliable.
SCANNED_BLANK_CONFIDENCE = 0.62

#: A cell counts as EMPTY when OCR found no word whose box overlaps it by more
#: than this fraction of the word. Generous, because OCR boxes routinely spill
#: a pixel or two past a rule.
WORD_OVERLAP_FRACTION = 0.35


@dataclass
class RuleSegment:
    """One detected rule, in PDF points."""

    position: float          #: y for a horizontal rule, x for a vertical one
    start: float             #: where it begins along its length
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass
class PageRules:
    horizontal: list[RuleSegment]
    vertical: list[RuleSegment]
    width: float
    height: float


def _ink_mask(page, dpi: int = RENDER_DPI):
    """Boolean array of form ink, plus the pixels-per-point scale."""
    import numpy as np

    pixmap = page.get_pixmap(dpi=dpi)
    img = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n)

    if pixmap.n >= 3:
        rgb = img[:, :, :3].astype(np.int16)
        grey = rgb.mean(axis=2)
        saturation = rgb.max(axis=2) - rgb.min(axis=2)
        ink = (grey < INK_MAX_GREY) & (saturation < INK_MAX_SATURATION)
    else:
        ink = img[:, :, 0] < INK_MAX_GREY

    scale = pixmap.width / page.rect.width if page.rect.width else 1.0
    return ink, scale


def _rules_along(ink, scale: float, horizontal: bool) -> list[RuleSegment]:
    """Long, thin runs of ink in one direction."""
    import numpy as np
    from scipy import ndimage

    span = max(int(round(MIN_RULE_LENGTH_PT * scale)), 3)
    element = np.ones((1, span)) if horizontal else np.ones((span, 1))
    opened = ndimage.binary_opening(ink, structure=element)

    labelled, count = ndimage.label(opened)
    if not count:
        return []

    found: list[RuleSegment] = []
    for box in ndimage.find_objects(labelled):
        height = (box[0].stop - box[0].start) / scale
        width = (box[1].stop - box[1].start) / scale
        thickness, length = (height, width) if horizontal else (width, height)
        if thickness > MAX_RULE_THICKNESS_PT or length < MIN_RULE_LENGTH_PT:
            continue
        if horizontal:
            found.append(RuleSegment(box[0].start / scale,
                                     box[1].start / scale, box[1].stop / scale))
        else:
            found.append(RuleSegment(box[1].start / scale,
                                     box[0].start / scale, box[0].stop / scale))

    return _cluster(found)


def _cluster(segments: list[RuleSegment]) -> list[RuleSegment]:
    """
    Merge rules whose positions are within `CLUSTER_TOLERANCE_PT`.

    A scanned line is several pixels thick, and its top and bottom edges become
    separate components. Merging takes the union of their extents so a rule
    broken by a speck is not reported as two short ones.
    """
    if not segments:
        return []
    segments = sorted(segments, key=lambda s: (s.position, s.start))
    merged = [segments[0]]
    for seg in segments[1:]:
        last = merged[-1]
        if seg.position - last.position <= CLUSTER_TOLERANCE_PT:
            last.start = min(last.start, seg.start)
            last.end = max(last.end, seg.end)
        else:
            merged.append(seg)
    return merged


def detect_rules(page, dpi: int = RENDER_DPI) -> PageRules:
    """Every ruled line on one rendered page, in PDF points."""
    ink, scale = _ink_mask(page, dpi)
    return PageRules(
        horizontal=_rules_along(ink, scale, horizontal=True),
        vertical=_rules_along(ink, scale, horizontal=False),
        width=page.rect.width,
        height=page.rect.height,
    )


def _spans(segment: RuleSegment, low: float, high: float,
           tolerance: float = 2.0) -> bool:
    """Whether a rule covers the band from `low` to `high`."""
    return segment.start <= low + tolerance and segment.end >= high - tolerance


def cells_from_rules(rules: PageRules) -> list[tuple[float, float, float, float]]:
    """
    Rebuild the grid: every (x0, top, x1, bottom) bounded by four rules.

    Built band by band rather than as one global grid. A form is a stack of
    rows whose column positions change from row to row — the enquiries block of
    an SBD 1 has four columns where the block above it has two — and a single
    global grid invents cells that were never drawn.
    """
    out: list[tuple[float, float, float, float]] = []
    horizontals = sorted(rules.horizontal, key=lambda s: s.position)

    for upper, lower in zip(horizontals, horizontals[1:]):
        top, bottom = upper.position, lower.position
        height = bottom - top
        if height < MIN_CELL_HEIGHT_PT or height > MAX_CELL_HEIGHT_PT:
            continue

        # Only verticals that actually cross this band bound its cells.
        walls = sorted(
            {v.position for v in rules.vertical if _spans(v, top, bottom)})
        if len(walls) < 2:
            continue

        for left, right in zip(walls, walls[1:]):
            if right - left >= MIN_CELL_WIDTH_PT:
                out.append((left, top, right, bottom))
    return out


def _words_in(words, box) -> list:
    """OCR words lying inside a cell."""
    x0, top, x1, bottom = box
    inside = []
    for word in words:
        wx0, wtop, wx1, wbottom = word.bbox
        area = max(wx1 - wx0, 0.1) * max(wbottom - wtop, 0.1)
        ox = max(0.0, min(x1, wx1) - max(x0, wx0))
        oy = max(0.0, min(bottom, wbottom) - max(top, wtop))
        if (ox * oy) / area >= WORD_OVERLAP_FRACTION:
            inside.append(word)
    return inside


#: Words whose tops are within this are on the same printed line.
LINE_BAND_PT = 4.0


def _text_of(words) -> str:
    """
    Read words in the order a person would.

    Sorting by raw (top, left) looks right and is not: OCR gives each word its
    own top, so two words on one line differ by a point or so and sort by that
    instead of by position across the page. "CELL PHONE NUMBER", which wraps
    inside its cell, came back as "PHONE CELL NUMBER" — a label no alias in the
    dictionary matches.

    So words are banded into lines first, then read left to right within each.
    """
    if not words:
        return ""
    ordered = sorted(words, key=lambda w: w.bbox[1])
    lines: list[list] = [[ordered[0]]]
    for word in ordered[1:]:
        if word.bbox[1] - lines[-1][0].bbox[1] <= LINE_BAND_PT:
            lines[-1].append(word)
        else:
            lines.append([word])
    return " ".join(
        w.text for line in lines for w in sorted(line, key=lambda w: w.bbox[0])
    ).strip()


def _merge_split_cells(blanks: list[Blank]) -> list[Blank]:
    """
    Join side-by-side blanks that are really one field.

    A rule that divides a row visually does not always divide it as a *field*:
    on the SBD 1, "NAME OF BIDDER" is one answer space spanning to the right
    margin, and detection split it at an internal rule into two cells carrying
    the same label. Left alone that writes the company name onto the same line
    twice, and the vector path — which reports one cell there — proves it is one
    field.

    Only merges blanks that share a label AND a row band AND touch, so two
    genuinely separate fields that happen to share a label (the CODE / NUMBER
    pairs in the telephone rows) are left alone.
    """
    if not blanks:
        return []

    merged: list[Blank] = []
    for blank in sorted(blanks, key=lambda b: (b.page_number, b.bbox[1], b.bbox[0])):
        previous = merged[-1] if merged else None
        if (previous is not None
                and previous.page_number == blank.page_number
                and previous.label_text == blank.label_text
                and abs(previous.bbox[1] - blank.bbox[1]) < 2.0
                and abs(previous.bbox[3] - blank.bbox[3]) < 2.0
                and blank.bbox[0] - previous.bbox[2] < 3.0):
            # `Blank` is frozen, so this replaces rather than mutates. Worth
            # keeping that way: these objects are handed to the fill engine and
            # the review gate, and a bbox that could change under them is how a
            # value ends up written somewhere nobody reviewed.
            merged[-1] = replace(
                previous,
                bbox=(previous.bbox[0], previous.bbox[1],
                      blank.bbox[2], previous.bbox[3]))
            continue
        merged.append(blank)
    return merged


def extract_scanned_blanks(path: str | Path, ocr_result=None,
                           pages: list[int] | None = None) -> list[Blank]:
    """
    Blanks in a scanned form, labelled from its OCR text.

    `ocr_result` may be passed in when the caller has already run OCR, because
    Vision bills per page and reading the same document twice costs twice.

    Returns the same `Blank` objects the vector path returns. Nothing
    downstream needs to know the difference, which is the point: the fill
    engine's refusals, the review gate and the export MAC all apply unchanged.
    """
    path = Path(path)
    try:
        import fitz  # noqa: F401
        import numpy  # noqa: F401
        from scipy import ndimage  # noqa: F401
    except ImportError as exc:
        log.warning("scanned_form: cannot run without %s", exc.name)
        return []

    if ocr_result is None:
        from agent_autofill.extraction.ocr import ocr_pdf

        ocr_result = ocr_pdf(path)
    if not getattr(ocr_result, "available", False):
        log.warning("scanned_form: no OCR for %s, so no labels to attach", path.name)
        return []

    import fitz

    words_by_page: dict[int, list] = {}
    for word in ocr_result.words:
        words_by_page.setdefault(word.page_number, []).append(word)

    blanks: list[Blank] = []
    doc = fitz.open(str(path))
    try:
        wanted = set(pages) if pages is not None else None
        for index in range(doc.page_count):
            if wanted is not None and index not in wanted:
                continue
            # Only pages OCR actually read: without words there are no labels,
            # and an unlabelled blank cannot be matched to a field anyway.
            if index not in words_by_page:
                continue

            rules = detect_rules(doc[index])
            cells = cells_from_rules(rules)
            words = words_by_page[index]
            blanks.extend(
                _merge_split_cells(_blanks_from_cells(cells, words, index)))
    finally:
        doc.close()

    log.info("scanned_form %s: %d blank(s) across %d page(s)",
             path.name, len(blanks), len(words_by_page))
    return blanks


def _blanks_from_cells(cells, words, page_index: int) -> list[Blank]:
    """
    Turn empty cells into labelled blanks.

    The label is the nearest text to the LEFT on the same row, falling back to
    the cell ABOVE — the same rule and the same order as the vector path's
    ruled-cell strategy, because it is the same visual convention being read.
    """
    contents = [(cell, _words_in(words, cell)) for cell in cells]
    filled = [(cell, text) for cell, text in
              ((c, _text_of(w)) for c, w in contents) if text]

    out: list[Blank] = []
    for cell, inside in contents:
        if inside:
            continue                      # has text: a label or an answer

        x0, top, x1, bottom = cell
        label, origin = None, "none"

        same_row = [(c, t) for c, t in filled
                    if abs(c[1] - top) < 2.0 and c[2] <= x0 + 2.0]
        if same_row:
            label = max(same_row, key=lambda ct: ct[0][2])[1]
            origin = "cell_left"
        else:
            above = [(c, t) for c, t in filled
                     if c[3] <= top + 2.0 and c[0] < x1 and c[2] > x0]
            if above:
                label = max(above, key=lambda ct: ct[0][3])[1]
                origin = "cell_above"

        if not label:
            continue                      # unlabelled: nothing to match against

        out.append(Blank(
            source="pdf",
            page_number=page_index,       # 0-based, as Blank documents
            bbox=(x0, top, x1, bottom),
            label_text=label,
            label_bbox=None,
            confidence=SCANNED_BLANK_CONFIDENCE,
            strategy="scanned_rule",
            label_origin=origin,
            notes=("labelled from OCR text",),
        ))
    return out
