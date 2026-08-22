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
* Nothing is drawn where a field was refused. Refusals are recorded in the
  result and shown in the review; they are not written onto the form. See
  `_mark_skipped` for what was there and why it went.
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
from agent_autofill.fill_engine.ambiguity_resolver import resolve as resolve_ambiguous
from agent_autofill.fill_engine.refusal_reasons import (
    classify_unfilled,
    explain_per_tender,
)
from agent_autofill.fill_engine.safe_fill_fields import decide

log = logging.getLogger("agent_autofill.pdf_filler")

#: The highlight behind a written value. Matches the .docx path's gold shading
#: closely enough to read as the same product, in the RGB floats fitz wants.
FILL_HIGHLIGHT = (0.953, 0.914, 0.839)

#: Retired. Nothing is written where a field is refused — see `_mark_skipped`.
#: The constant stays so importers do not break; it is no longer drawn.
SKIP_MARKER = "[ ! ]"

#: Point size for written values in the handwriting face.
#:
#: This was 8.5, chosen when values were drawn in Helvetica. They are drawn in
#: Patrick Hand now, which is substantially smaller at the same point size, and
#: nobody re-measured — so switching the font silently shrank every value by a
#: fifth, and on a printed or re-scanned form the result was close to
#: illegible.
#:
#: Measured on this machine with the real face:
#:
#:     "CairoAI" at 8.5pt   Patrick Hand 22.74pt   Helvetica 28.34pt
#:     ratio 0.802  ->  Patrick Hand needs 10.6pt to match Helvetica 8.5
#:
#: The cost is real and was checked rather than assumed. On the owner's actual
#: 651-blank pack, refusals rise from 19.1% at 8.5pt to 25.5% at 10.6pt — and
#: the curve is smooth, about 1.5 points of refusal per half point of type,
#: with no cliff. Those refusals are the fit check working: the alternative is
#: text overflowing its cell on a document submitted to an organ of state, and
#: a field left for a person is visibly unfinished in a way illegible ink is
#: not.
FONT_SIZE = 10.6

#: The built-in fallback, used only when the handwriting face will not load.
#: It must NOT follow FONT_SIZE: Helvetica is wider, and 10.6 there would be a
#: quarter larger than anything ever looked. This is the original size, which
#: is the appearance the new value was chosen to reproduce.
FALLBACK_FONT_SIZE = 8.5

SKIP_FONT_SIZE = 7.5

#: A blank narrower than this cannot hold anything meaningful.
MIN_BLANK_WIDTH = 24.0

#: Fallback glyph width as a fraction of point size for Helvetica, used only if
#: PyMuPDF cannot measure a string. It is an average, so it under-estimates
#: wide strings: "TCS0001234567" measured 55pt by this rule and rendered past
#: the right-hand rule of its cell into the column beside it.
CHAR_WIDTH_RATIO = 0.5

#: A handwriting face for the values, so a completed form reads the way a
#: person filling it in by hand would leave it.
#:
#: Patrick Hand, SIL Open Font License — free to embed and redistribute, which
#: the handwriting faces shipped with Windows (Ink Free, Lucida Handwriting,
#: Comic Sans) are NOT. Bundling one of those in a PDF this server generates
#: and hands to a user is a licensing problem, not a stylistic one.
#:
#: Chosen for legibility rather than flourish: it is neat handwritten printing,
#: not cursive. A VAT number an evaluator misreads is a rejected bid.
#:
#: THE HIGHLIGHT STAYS. Handwriting-styled text is the one change here that
#: works against this module's own rule — "a reader can see at a glance what
#: came from CairoAI rather than from a person" — and the gold band is what
#: keeps that true. A form where machine-placed values are indistinguishable
#: from a person's, beside a signature block this engine refuses to fill
#: precisely because signatures must be human, is not something to ship by
#: accident. Removing the highlight is a deliberate decision for someone else
#: to make, out loud.
HANDWRITING_FILE = Path(__file__).resolve().parent / "fonts" / "PatrickHand-Regular.ttf"

#: The name the face is registered under inside each generated PDF.
HANDWRITING_ALIAS = "cairohand"

_hand_font = None
_hand_loaded = False


def _handwriting():
    """
    The embedded handwriting face, or None to fall back to Helvetica.

    Loaded once and cached. A missing or unreadable font file must not stop a
    document being filled — the values matter, the styling does not — so this
    degrades to Helvetica and says so in the log rather than raising.
    """
    global _hand_font, _hand_loaded
    if _hand_loaded:
        return _hand_font
    _hand_loaded = True
    try:
        import fitz

        if HANDWRITING_FILE.exists():
            _hand_font = fitz.Font(fontfile=str(HANDWRITING_FILE))
        else:
            log.warning("handwriting font missing at %s; using Helvetica",
                        HANDWRITING_FILE)
    except Exception:  # noqa: BLE001 - styling must never fail a fill
        log.exception("could not load the handwriting font; using Helvetica")
        _hand_font = None
    return _hand_font

#: Clearance kept inside the blank, in points: 2 for the left inset that
#: `_write_value` applies and 2 so a glyph never touches the closing rule.
FIT_PADDING = 4.0


def _text_width(text: str, size: float = FONT_SIZE) -> float:
    """
    The real rendered width of `text`, in the face it will actually be drawn in.

    Measuring in one font and drawing in another is how a value ends up past
    the rule of its cell while every check said it fitted. Patrick Hand happens
    to be narrower than Helvetica — "TCS-TESTPIN" is 42.2pt against 55.7pt — so
    measuring in Helvetica would refuse values that fit comfortably.
    """
    face = _handwriting()
    if face is not None:
        try:
            return face.text_length(str(text), fontsize=size)
        except Exception:  # noqa: BLE001
            pass
    try:
        import fitz

        return fitz.get_text_length(str(text), fontname="helv", fontsize=size)
    except Exception:  # noqa: BLE001 - measurement is an optimisation, not a gate
        return len(str(text)) * size * CHAR_WIDTH_RATIO


def _fits(text: str, width: float, size: float = FONT_SIZE) -> bool:
    return _text_width(text, size) <= max(width - FIT_PADDING, 0)


def _identify_unknowns(company_id: str, blanks, match_label_fn, profile) -> dict:
    """
    Ask what the labels nobody could place are asking for. {label: answer}.

    ONE PASS, BEFORE ANY WRITING. The alternative — asking as each blank comes
    up — is one API call per blank on a 145-page pack.

    Only genuinely unknown labels are sent. A label the dictionary places, a
    lesson already covers, the blocklist refuses, or `classify_unfilled` already
    describes correctly (a sworn declaration, a price cell, this bid's terms) is
    not worth an API call and, in the blocked case, is not something to hand to
    an external service at all.

    Every failure here is silent and total: no key, no quota, no network, a
    malformed answer — the fill proceeds exactly as it did before this existed.
    """
    from agent_autofill.extraction import label_classifier, learned_labels

    unknown, seen = [], set()
    for blank in blanks:
        label = (getattr(blank, "label_text", "") or "").strip()
        key = learned_labels.normalise(label)
        if not key or key in seen:
            continue
        seen.add(key)

        match = match_label_fn(label)
        if getattr(match, "canonical", None):
            continue
        if resolve_ambiguous(label, profile)[0]:
            continue
        if learned_labels.lookup(company_id, label) is not None:
            continue
        if is_blocked(label).blocked:
            continue
        if classify_unfilled(label, match)[0] != "unmatched":
            continue
        unknown.append(label)

    if not unknown:
        return {}

    log.info("asking about %d unrecognised label(s)", len(unknown))
    try:
        return label_classifier.classify_and_remember(company_id, unknown)
    except Exception as exc:  # noqa: BLE001 - a fill must never depend on this
        log.error("label identification unavailable: %s", exc)
        return {}


def fill_pdf(source, output, profile: dict, match_label_fn,
             company_id: str = None) -> FillResult:
    """
    Produce a filled COPY of a PDF form.

    Mirrors `fill_docx`'s signature and returns the same `FillResult`, so the
    review gate, the pack aggregation and the export path all work on a PDF
    draft without knowing it is one.

    `company_id` turns on the two things that need to know whose forms these
    are: lessons taught on this company's previous tenders, and asking Claude
    what a label means when nothing else can say. Without it the fill behaves
    exactly as it always has — neither is a decision, both are ways of getting
    a canonical field name, and every field name still goes through
    `is_blocked` and `decide` below.
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
        # Nothing found in the vector graphics. That is either a document with
        # no form in it, or a scan — where there ARE no vector graphics to read
        # and pdfplumber can only ever return zero. Recovering the rules from
        # the image is the difference between "no draft could be produced" and
        # a filled SBD 1, so it is worth the second look.
        blanks = _scanned_blanks(source)
    if not blanks:
        return FillResult(source_path=str(source), output_path=str(output),
                          filled=[], skipped=[], context=set())

    # What the dictionary could not place, asked about once and remembered.
    # Done before the document is opened so a slow or failing API call cannot
    # leave a half-written file behind.
    identified: dict = {}
    not_a_field: set = set()
    if company_id:
        from agent_autofill.extraction import learned_labels

        identified = _identify_unknowns(company_id, blanks, match_label_fn, profile)
        # Read AFTER identification, so this pack's answers are already in it.
        not_a_field = {
            lesson["normalised"] for lesson in learned_labels.lessons(company_id)
            if lesson["kind"] == learned_labels.KIND_NOT_A_FIELD
        }

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
            # `Blank.notes` is a TUPLE, and `is_blocked` regex-searches its
            # `section` argument. This read the tuple straight through, which
            # worked only because the vector path leaves notes empty — an empty
            # tuple is falsy and became None. Any blank carrying a note raised
            # `TypeError: expected string or bytes-like object, got 'tuple'`
            # from inside the blocklist, which is the worst place in this
            # package to throw: it is the check standing between a stored
            # profile and a signed declaration.
            notes = getattr(blank, "notes", None)
            section = (" ".join(notes) if isinstance(notes, (list, tuple))
                       else notes) or None
            block = is_blocked(label, section=section)
            if block.blocked:
                _mark_skipped(page, bbox)
                skipped.append(SkippedField(
                    label=label or "(unlabelled)",
                    reason=block.message, category="blocked", location=location))
                continue

            match = match_label_fn(label) if label else None
            canonical = getattr(match, "canonical", None) if match else None
            score = getattr(match, "score", 0.0) if match else 0.0

            # A label the dictionary refuses as too general may still have only
            # one possible answer for THIS company. "ADDRESS" is ambiguous
            # between postal and physical in the abstract; when a company has
            # recorded the same string for both, it is not ambiguous at all,
            # and refusing it leaves an empty line on a bid for no reason.
            #
            # Only resolves BETWEEN fields that are already safe to fill.
            # `is_blocked` has already run above and still governs.
            if not canonical and label:
                resolved, _ = resolve_ambiguous(label, profile)
                if resolved:
                    canonical, score = resolved, 100.0

            # A label this company has already explained, or that Claude has
            # just named. `apply_learning` holds the ordering rule — a
            # confident dictionary match always wins — and it is called here
            # rather than reimplemented, so there is one copy of it.
            #
            # This produces a FIELD NAME, never a value. What gets written is
            # still whatever `decide` reads out of the profile for that field,
            # under `is_blocked` above.
            if not canonical and label and company_id:
                learned, _source = learned_labels.apply_learning(
                    company_id, label, match)
                if learned:
                    canonical, score = learned, 100.0

            if not canonical:
                # Established as not a field at all — a section heading, a
                # table caption, a sentence of instructions. It was never a
                # question, so listing it as one is noise on a review screen
                # that already has too much on it.
                if label and not_a_field and \
                        learned_labels.normalise(label) in not_a_field:
                    continue

                # Understood, and still refused. The answer is a fact about
                # THIS tender, which no company profile could hold — so say
                # what it is asking for instead of claiming not to know.
                answer = identified.get(label)
                if answer and answer.get("kind") == "per_tender":
                    skipped.append(SkippedField(
                        label=label,
                        reason=explain_per_tender(answer.get("asking_for")),
                        category="per_tender", location=location))
                    continue


                # Deliberately NOT marked on the page. `[ ! ]` means "a field
                # was refused on purpose"; an unmatched cell is usually not a
                # field at all. On the real SBD 1 the ruled-cell extractor
                # offered up a table header ("BIDDING PROCEDURE ENQUIRIES MAY
                # BE DIRECTED TO") and a footnote in italics, and both came
                # back stamped in red on the user's document.
                #
                # This mirrors `review_gate.ADVISORY_CATEGORIES`, where the
                # same category was dropped from the confirmation list for the
                # same reason: it is a note about extraction, not a decision
                # about the form.
                # One sentence for every refusal taught the user to distrust
                # all of them: on a real RFQ this fired 41 times, and 38 were
                # the system working correctly — SBD 4 declaration fields,
                # price cells, and commercial terms for that bid. Saying "I
                # could not tell" about a sworn declaration is not humility,
                # it is a wrong description of a right decision.
                category, reason = classify_unfilled(label, match)
                skipped.append(SkippedField(
                    label=label or "(unlabelled)",
                    reason=reason, category=category, location=location))
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
            # Measure at the size this blank will actually be written at. The
            # fit check and the drawing must agree, or a value passes the check
            # at one size and overflows its cell at another.
            fill_size = _fill_size_for(page, bbox)
            if width < MIN_BLANK_WIDTH or not _fits(value, width, fill_size):
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

            _write_value(page, bbox, value, seed=f"{label}|{location}",
                         size=fill_size)
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


def _scanned_blanks(source: Path) -> list:
    """
    Blanks recovered from the page image, for a PDF with no vector graphics.

    Only attempted when the document actually looks like a scan, because the
    detection renders every page and the OCR behind the labels is billed per
    page. A text PDF that simply has no form in it must not pay for either.

    Returns [] on any failure. A scan that cannot be read is the situation the
    caller was already in; it must not become an exception thrown out of the
    fill engine.
    """
    try:
        from agent_autofill.extraction.ocr import needs_ocr
        from agent_autofill.extraction.scanned_form_extractor import (
            extract_scanned_blanks,
        )
    except ImportError as exc:  # noqa: BLE001 - optional path
        log.warning("scanned fill unavailable: %s", exc)
        return []

    try:
        if not needs_ocr(source):
            return []
        found = extract_scanned_blanks(source)
        log.info("scanned fill %s: recovered %d blank(s) from the image",
                 source.name, len(found))
        return found
    except Exception as exc:  # noqa: BLE001
        log.warning("scanned blank detection failed on %s: %s", source.name, exc)
        return []



# --- handwriting variation ----------------------------------------------------
#
# P0-3. Every value sat on a perfect baseline, at a uniform size, in a uniform
# ink. A handwriting face on a perfect grid reads as a font, not as writing.
#
# DETERMINISTIC, NOT RANDOM. The jitter is seeded from the field key, so the
# same document always renders identically. A draft that changes every time it
# is regenerated cannot be checked, diffed or trusted — and the export MAC
# binds content that must not drift, so randomness here would invalidate a
# reviewed export on re-render.
#
# Deliberately subtle. Exaggerated rotation reads as a filter, and this
# document goes to a procurement officer.

#: Maximum vertical wander, in points. A pen does not find the same baseline.
JITTER_Y = 0.55
#: Maximum extra horizontal inset. Nobody starts every entry at the same x.
JITTER_X = 1.30
#: Maximum rotation in degrees, either direction.
JITTER_ROTATE = 0.55
#: Fractional size variation between fields, as a hand produces.
JITTER_SIZE = 0.035


def _jitter(seed_text: str) -> tuple[float, float, float, float, tuple]:
    """
    (dx, dy, degrees, size_scale, colour) for one field, stable for its key.

    Derived from a digest rather than `random`, so it depends on nothing but
    the seed — no module state, no ordering, no time.
    """
    import hashlib

    digest = hashlib.sha256((seed_text or "").encode("utf-8")).digest()

    def unit(index: int) -> float:
        """A value in [-1, 1] from two digest bytes."""
        raw = (digest[index] << 8) | digest[index + 1]
        return (raw / 65535.0) * 2.0 - 1.0

    dx = abs(unit(0)) * JITTER_X          # only ever inset further, never left
    dy = unit(2) * JITTER_Y
    degrees = unit(4) * JITTER_ROTATE
    size_scale = 1.0 + unit(6) * JITTER_SIZE

    # Ink varies slightly around the blue-black a pen leaves. Small enough that
    # no field looks like a different colour, large enough that no two are the
    # identical RGB.
    r = 0.05 + unit(8) * 0.02
    g = 0.08 + unit(10) * 0.02
    b = 0.25 + unit(12) * 0.03
    colour = (max(0.0, min(1.0, r)), max(0.0, min(1.0, g)), max(0.0, min(1.0, b)))

    return dx, dy, degrees, size_scale, colour



#: How far a single letter may depart from the value's own size. A hand does
#: not draw two characters identically, and a line where every glyph is the
#: same height reads as a font no matter how handwritten the face is.
GLYPH_SIZE_RANGE = 0.07

#: Occasionally a letter is noticeably bigger or smaller than its neighbours —
#: the owner asked for outliers, not uniform wobble. This is how often, and how
#: far, one is allowed to depart.
GLYPH_OUTLIER_RATE = 0.14
GLYPH_OUTLIER_RANGE = 0.17

#: Vertical wander per letter, in points. Small: a hand keeps the baseline
#: roughly, it does not scatter letters.
GLYPH_BASELINE_JITTER = 0.5


def _glyph_variation(seed_text: str, count: int):
    """
    Per-letter (size_scale, dy) pairs, stable for a given seed.

    Derived from a digest that is extended as needed rather than from `random`,
    for the same reason `_jitter` is: the same document must render identically
    every time. The export MAC binds the bytes of the file, so writing that
    drifts between renders would break verification on a document nobody
    changed.
    """
    import hashlib

    out = []
    block = b""
    for i in range(count):
        if i * 4 + 4 > len(block):
            block += hashlib.sha256(
                (seed_text or "").encode("utf-8") + b"|glyph|" + str(i).encode()
            ).digest()
        a = ((block[i * 4] << 8) | block[i * 4 + 1]) / 65535.0
        b = ((block[i * 4 + 2] << 8) | block[i * 4 + 3]) / 65535.0

        if a < GLYPH_OUTLIER_RATE:
            # An outlier. Rescaled from the low slice of `a` so the decision and
            # the magnitude do not correlate into a visible pattern.
            spread = GLYPH_OUTLIER_RANGE
            unit = (a / GLYPH_OUTLIER_RATE) * 2.0 - 1.0
        else:
            spread = GLYPH_SIZE_RANGE
            unit = ((a - GLYPH_OUTLIER_RATE) / (1.0 - GLYPH_OUTLIER_RATE)) * 2.0 - 1.0

        out.append((1.0 + unit * spread, (b * 2.0 - 1.0) * GLYPH_BASELINE_JITTER))
    return out


def _draw_handwritten(page, point, text: str, size: float, colour,
                      degrees: float, seed: str) -> None:
    """
    Draw `text` one letter at a time so no two are the same size.

    Each glyph is advanced by ITS OWN measured width at ITS OWN size, so the
    line stays correctly spaced — advancing by the nominal width would make the
    letters creep apart or collide as the sizes drift.
    """
    import fitz

    face = _handwriting()
    variation = _glyph_variation(seed, len(text))
    x = point.x
    for ch, (scale, dy) in zip(text, variation):
        glyph_size = size * scale
        if ch.strip():
            page.insert_text(fitz.Point(x, point.y + dy), ch,
                             fontsize=glyph_size, fontname=HANDWRITING_ALIAS,
                             fontfile=str(HANDWRITING_FILE), color=colour,
                             morph=(fitz.Point(x, point.y), fitz.Matrix(degrees)))
        x += face.text_length(ch, fontsize=glyph_size)


# --- sizing the writing to the form -------------------------------------------
#
# A person writes to the size of the form in front of them. On the owner's
# hand-filled SBD 6.1 the handwriting sits at the same height as the printed
# body text, and that is what makes it read as belonging to the page.
#
# A single constant cannot do that, because forms are not printed at one size.
# The real pack measures 10.0pt and 11.0pt across 57,000 characters, with 9,
# 12, 14 and 16pt elsewhere. So the size is taken from the printed text beside
# each blank.
#
# WHY THE PREVIOUS FIX WAS NOT ENOUGH. Raising the constant from 8.5 to 10.6
# restored parity with how values looked BEFORE the face changed to Patrick
# Hand. But that earlier appearance was itself too small against the form: at
# 10.6 the effective size is 10.6 x 0.80 = 8.5pt beside body text printed at
# 10-11pt. The right reference is the form, not the product's own history.

#: Patrick Hand renders about 80% the width of Helvetica at the same point
#: size, measured on this machine: "CairoAI" is 22.74pt against 28.34pt.
#: A point size is divided by this to reach the same apparent size.
HAND_WIDTH_RATIO = 0.802

#: Never smaller than this, whatever the form says. Below it a value does not
#: survive being printed and rescanned, which is how these are submitted.
MIN_FILL_SIZE = 10.0

#: Nor larger, so an oversized heading beside a blank cannot drive the writing
#: to a size no cell can hold.
MAX_FILL_SIZE = 15.0

#: Used when a page has no text layer and no OCR heights: the commonest body
#: size on South African tender forms.
ASSUMED_PRINT_SIZE = 10.5

#: How far from a blank a printed span may be and still be "beside" it.
_NEAR_POINTS = 26.0

_page_size_cache: dict = {}


def _printed_sizes(page) -> list:
    """
    (y_centre, size) for every printed span on the page, cached.

    Read from the page itself rather than passed in, so this works wherever
    _write_value is called from and needs no change to the extractor.
    """
    key = (id(page.parent), page.number)
    if key in _page_size_cache:
        return _page_size_cache[key]

    spans = []
    try:
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = float(span.get("size") or 0)
                    text = (span.get("text") or "").strip()
                    # Whitespace-only spans carry a size but no ink, and a
                    # single character is too weak a sample to size from.
                    if size <= 0 or len(text) < 2:
                        continue
                    y0, y1 = span["bbox"][1], span["bbox"][3]
                    spans.append(((y0 + y1) / 2.0, size))
    except Exception:  # noqa: BLE001 - sizing must never fail a fill
        log.exception("could not read printed sizes on page %s", page.number)

    _page_size_cache[key] = spans
    return spans


def _fill_size_for(page, bbox) -> float:
    """
    The point size to write this blank at, derived from the form around it.

    Falls back through: printed text beside the blank -> anything printed on
    the page -> ASSUMED_PRINT_SIZE. A scanned page has no text layer at all,
    and lands on the assumption, which is still above the floor.
    """
    try:
        y_centre = (float(bbox[1]) + float(bbox[3])) / 2.0
        spans = _printed_sizes(page)

        near = [s for y, s in spans if abs(y - y_centre) <= _NEAR_POINTS]
        if not near:
            near = [s for _, s in spans]

        if near:
            # Median rather than mean: one heading beside a blank should not
            # drag the size of everything in that row.
            near.sort()
            printed = near[len(near) // 2]
        else:
            printed = ASSUMED_PRINT_SIZE
    except Exception:  # noqa: BLE001
        printed = ASSUMED_PRINT_SIZE

    target = printed / HAND_WIDTH_RATIO
    return max(MIN_FILL_SIZE, min(MAX_FILL_SIZE, target))


def _write_value(page, bbox, value: str, seed: str = "", size: float = None) -> None:
    """
    Highlight the blank, then write the value inside it.

    `seed` makes the handwriting variation deterministic per field. It is
    the label and location, so the same document renders identically every
    time — the export MAC binds content that must not drift.
    """
    import fitz

    x0, y0, x1, y1 = [float(v) for v in bbox]
    # The highlight is drawn whatever face the value uses. With handwriting it
    # matters more, not less: it is the only thing left saying a machine put
    # this here.
    #
    # OVERLAY, NOT UNDERLAY. This was `overlay=False`, which draws beneath the
    # existing content. That is invisible on a scan: the entire page is one
    # image, so the band went behind the bitmap and no reader ever saw it. The
    # safeguard was missing on exactly the documents where the values are least
    # distinguishable from handwriting — checked by sampling the pixel inside a
    # filled cell, which came back pure white on both a scanned fill and the
    # one before the font changed.
    #
    # Semi-transparent so the form underneath still reads: the cell's own rules
    # and any faint print stay visible through the wash rather than being
    # painted out.
    page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=None,
                   fill=FILL_HIGHLIGHT, overlay=True, fill_opacity=0.45)

    # Per-field variation, seeded from the field's own key so the same document
    # always renders identically. A perfect baseline at a uniform size in a
    # uniform ink reads as a font however handwritten the face is.
    dx, dy, degrees, size_scale, colour = _jitter(seed or f"{x0:.1f},{y0:.1f}")
    base_size = _fill_size_for(page, bbox) if size is None else size

    # Baseline nudged up from the bottom edge so the text sits on the rule
    # rather than under it, then wandered slightly.
    point = fitz.Point(x0 + 2 + dx, y1 - 2.5 + dy)

    if _handwriting() is not None:
        try:
            # `rotate` takes whole degrees, so a fractional angle is applied
            # through the text matrix instead — that is what breaks the typeset
            # feel, and rounding it to 0 would lose the effect entirely.
            _draw_handwritten(page, point, value, base_size * size_scale,
                              colour, degrees, seed or f"{x0:.1f},{y0:.1f}")
            return
        except Exception:  # noqa: BLE001 - fall through to the built-in face
            log.exception("could not draw %r in the handwriting font", value[:40])

    page.insert_text(point, value, fontsize=FALLBACK_FONT_SIZE, fontname="helv",
                     color=(0, 0, 0))


def _mark_skipped(page, bbox) -> None:
    """
    Deliberately does nothing. Kept as the one place that USED to write on the
    page, so the reason is recorded where somebody would go to add it back.

    This drew a red `[ ! ]` into every blank that was refused. The intent was
    that a field left on purpose should not look like one that was missed —
    which is a real problem, and this was the wrong place to solve it.

    The owner: "i dont like the exclamation marks that it puts on fields it
    cant answer it ends up staying on the hard copy so no red exclamation marks
    remove that feature."

    He is right, and the SBD 4 bidder's disclosure on his RFQ shows why: nine
    red marks stamped down the Identity Number column of a table that is
    CORRECTLY empty — no director is employed by the state, so there is nothing
    to declare. The marks are printed, signed and handed to an organ of state
    looking like defacement of a statutory form. A draft aid became permanent
    the moment he pressed print.

    Nothing is lost by removing it. Every refusal is still recorded in
    `FillResult.skipped` with its reason and page, still listed in the review,
    and still blocks an export where it always did. The record was never the
    ink — the ink was a second copy of it on somebody else's form.
    """
    return
