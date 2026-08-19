"""
Filled values are readable, and read as handwriting rather than as a font.

Two defects from the owner's first real pack:

P0-2. FONT_SIZE was 8.5, chosen when values were drawn in Helvetica. They are
drawn in Patrick Hand now, which is ~80% the width at the same point size, so
switching the face silently shrank every value by a fifth and nobody
re-measured. On a printed or re-scanned form the result was close to illegible.

P0-3. Every value sat on a perfect baseline at a uniform size in a uniform ink.
A handwriting face on a perfect grid reads as a font, not as writing.

The determinism test is the one that matters most. The export MAC binds
document content; if the jitter were random, regenerating a reviewed draft
would invalidate its own signature and no draft could be diffed or checked.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_autofill.fill_engine import pdf_filler

# Resolved defensively so this module can run against the version that had the
# bug, rather than failing on collection with an ImportError.
_jitter = getattr(pdf_filler, "_jitter", None)
FALLBACK_FONT_SIZE = getattr(pdf_filler, "FALLBACK_FONT_SIZE", pdf_filler.FONT_SIZE)


# --- P0-2: legible ------------------------------------------------------------

def test_the_handwriting_size_matches_what_helvetica_looked_like():
    """
    Not an arbitrary number. Patrick Hand at FONT_SIZE must render about as
    wide as Helvetica did at the 8.5 it replaced, or the form is smaller than
    it ever was meant to be.
    """
    import fitz

    sample = "CairoAI (Pty) Ltd"
    hand = pdf_filler._text_width(sample, pdf_filler.FONT_SIZE)
    helvetica_at_original = fitz.Font("helv").text_length(sample, 8.5)

    ratio = hand / helvetica_at_original
    assert 0.95 <= ratio <= 1.25, (
        f"values render at {ratio:.0%} of their original apparent size"
    )


def test_the_size_is_in_the_legible_range():
    assert pdf_filler.FONT_SIZE >= 10.0, "too small to survive a print and scan"
    assert pdf_filler.FONT_SIZE <= 12.0, "large enough to overflow ordinary cells"


def test_the_helvetica_fallback_did_not_grow_with_it():
    """
    The fallback shares nothing but a name. Helvetica is wider, so following
    FONT_SIZE would make that path a quarter larger than anything ever looked.
    """
    assert FALLBACK_FONT_SIZE == 8.5
    assert FALLBACK_FONT_SIZE < pdf_filler.FONT_SIZE


def test_the_fit_check_measures_the_face_actually_used():
    """
    Measuring in one font and drawing in another is how a value ends up past
    the rule of its cell while every check said it fitted.
    """
    wide = "DONINGTON VALE (PTY) LTD"
    measured = pdf_filler._text_width(wide, pdf_filler.FONT_SIZE)
    assert pdf_filler._fits(wide, measured + pdf_filler.FIT_PADDING + 1,
                            pdf_filler.FONT_SIZE)
    assert not pdf_filler._fits(wide, measured * 0.5, pdf_filler.FONT_SIZE)


# --- P0-3: handwritten, and deterministic ------------------------------------

@pytest.mark.skipif(_jitter is None, reason="jitter not implemented")
def test_the_same_field_always_renders_identically():
    """
    THE important one. The export MAC binds content: a draft that changes on
    every render cannot be checked, diffed, or trusted, and a reviewed export
    would stop verifying against itself.
    """
    assert _jitter("company_name|page 1") == _jitter("company_name|page 1")
    assert _jitter("x") == _jitter("x")


@pytest.mark.skipif(_jitter is None, reason="jitter not implemented")
def test_different_fields_vary():
    """A uniform offset is just a different grid."""
    a = _jitter("company_name|page 1")
    b = _jitter("registration_number|page 1")
    c = _jitter("company_name|page 7")
    assert a != b and a != c


@pytest.mark.skipif(_jitter is None, reason="jitter not implemented")
@pytest.mark.parametrize("seed", [f"field_{i}|page {i}" for i in range(40)])
def test_the_variation_stays_subtle(seed):
    """
    Exaggerated rotation or wobble reads as a filter, and this document goes to
    a procurement officer.
    """
    dx, dy, degrees, size_scale, colour = _jitter(seed)

    assert 0 <= dx <= 2.0, "horizontal offset large enough to look misaligned"
    assert abs(dy) <= 1.0, "baseline wander large enough to look broken"
    assert abs(degrees) <= 1.0, "rotation large enough to read as an effect"
    assert 0.95 <= size_scale <= 1.05, "size variation large enough to look wrong"

    r, g, b = colour
    assert 0.0 <= r <= 0.15 and 0.0 <= g <= 0.2 and 0.1 <= b <= 0.4, (
        "ink drifted away from blue-black"
    )


@pytest.mark.skipif(_jitter is None, reason="jitter not implemented")
def test_the_ink_varies_without_changing_colour():
    """Every field must not be the identical RGB, and none may look like a
    different pen."""
    inks = {_jitter(f"f{i}")[4] for i in range(30)}
    assert len(inks) > 20, "the ink is effectively uniform"


@pytest.mark.skipif(_jitter is None, reason="jitter not implemented")
def test_the_jitter_never_pulls_a_value_left_out_of_its_cell():
    """
    dx is an inset, never negative: a value nudged left of its blank sits on
    the rule or in the label beside it.
    """
    assert all(_jitter(f"f{i}")[0] >= 0 for i in range(50))


# --- the safeguard the brief says must survive --------------------------------

def test_the_highlight_is_still_drawn_over_the_page():
    """
    The gold band is what tells a reader which entries came from CairoAI. It
    matters MORE as the text becomes more convincingly handwritten, not less —
    this is the line between "drafted for you to check" and "forged".
    """
    source = Path(pdf_filler.__file__).read_text(encoding="utf-8")
    assert "FILL_HIGHLIGHT" in source
    assert "overlay=True" in source, (
        "the highlight would be drawn beneath the page content and be invisible "
        "on a scan"
    )


def test_the_highlight_was_not_faded_to_sell_the_effect():
    """The brief: do not make it lighter."""
    import re

    source = Path(pdf_filler.__file__).read_text(encoding="utf-8")
    opacity = float(re.search(r"fill_opacity=([\d.]+)", source).group(1))
    assert opacity >= 0.4, f"highlight faded to {opacity}"


def test_refusals_are_still_marked_in_place():
    """A blank left on purpose must stay visibly different from one missed."""
    source = Path(pdf_filler.__file__).read_text(encoding="utf-8")
    assert "SKIP_MARKER" in source and "[ ! ]" in source
