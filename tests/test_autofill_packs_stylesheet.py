"""
Every class the packs UI emits has a rule.

autofill_packs.js emits 58 `ap-*` class names and 57 of them had no CSS rule
anywhere, so the page rendered on browser defaults. That is what the "white
blocks" were: native <button> and <input> elements drawing operating-system
chrome because nothing was styling them. It is also why the page read as a wall
of text — with no type scale, a warning, a field label and a value all came out
at the same size.

The 58th was `.ap-progress`, styled by a rule in style.css written for a
different `ap-progress-*` widget whose other six classes this JS never emits.

This is a coverage test. The failure it guards against is silent: someone adds
a class in the JS, no rule exists, and the element renders unstyled in a corner
of a page nobody re-checks.
"""

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "static" / "autofill_packs.js"
CSS = ROOT / "static" / "autofill_packs.css"
WORKSPACE = ROOT / "static" / "workspace.html"
STYLE = ROOT / "static" / "style.css"


def emitted_classes() -> set:
    """Every ap-* class name the JS puts on an element."""
    source = JS.read_text(encoding="utf-8")
    names = set()
    for quoted in re.findall(r"'(ap-[a-z0-9 -]+)'", source):
        names.update(n for n in quoted.split() if n.startswith("ap-"))
    return names


def styled_classes() -> set:
    """Every ap-* class this stylesheet defines a rule for."""
    return set(re.findall(r"\.(ap-[a-z0-9-]+)", CSS.read_text(encoding="utf-8")))


def test_the_stylesheet_exists_and_is_linked():
    assert CSS.exists()
    html = WORKSPACE.read_text(encoding="utf-8")
    assert "autofill_packs.css" in html, "the stylesheet is never loaded"
    assert re.search(r"autofill_packs\.css\?v=[\d.]+", html), (
        "the stylesheet has no cache-buster; a change would not reach browsers"
    )


def test_every_emitted_class_has_a_rule():
    """The coverage gap itself. 57 of 58 had none."""
    missing = sorted(emitted_classes() - styled_classes())
    assert not missing, f"{len(missing)} class(es) render unstyled: {missing}"


def test_the_stylesheet_does_not_define_classes_nothing_emits():
    """
    The other direction, and the reason .ap-progress was confusing: style.css
    carries rules for six ap-progress-* classes this JS never emits. Dead rules
    make the next person think a component is styled when it is not.
    """
    extra = sorted(styled_classes() - emitted_classes())
    assert not extra, f"rules for classes nothing emits: {extra}"


def test_it_loads_after_style_css_so_the_ap_progress_collision_resolves():
    """
    .ap-progress is defined in both files. Equal specificity means load order
    decides, so this file must come second or the dead widget's gold-bordered
    block wins.
    """
    html = WORKSPACE.read_text(encoding="utf-8")
    assert "ap-progress" in STYLE.read_text(encoding="utf-8"), (
        "the collision is gone; this test can go with it"
    )
    assert html.index("autofill_packs.css") > html.index("style.css")


# --- what the brief asked for -------------------------------------------------

@pytest.mark.parametrize("control", ["ap-input", "ap-value-check", "ap-ack-btn",
                                     "ap-export", "ap-preview-link", "ap-download",
                                     "ap-link-btn", "ap-file-remove"])
def test_every_native_control_is_styled(control):
    """
    An unstyled <button> or <input> draws its own chrome. Leaving one out
    brings a white block back.
    """
    css = CSS.read_text(encoding="utf-8")
    assert f".{control}" in css


def test_preview_draft_is_a_button_and_not_an_export():
    """
    It opens a filled PDF in a new tab. It is not an export and must not look
    like one — only the action that releases a document is filled gold.
    """
    css = CSS.read_text(encoding="utf-8")

    def declarations_for(selector: str) -> str:
        """Every declaration block whose selector list names this class."""
        out = []
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if re.search(rf"\.{re.escape(selector)}\b(?![-\w])", sel):
                out.append(body)
        return "\n".join(out)

    preview = declarations_for("ap-preview-link")
    export = declarations_for("ap-export")

    assert "background: transparent" in preview, (
        "PREVIEW DRAFT is not outlined; it reads as the primary action"
    )
    assert "var(--ap-gold)" in export and "background: var(--ap-gold)" in export, (
        "EXPORT is not the filled primary action"
    )
    assert "background: var(--ap-gold);" not in preview, (
        "PREVIEW DRAFT is filled gold like an export"
    )
    # And it is a button rather than a bare underlined link.
    assert "text-decoration: none" in preview


def test_state_colour_is_not_the_gold_accent():
    """
    A field needing review must not compete with the accent. The brief is
    explicit that semantic colour is separate.
    """
    css = CSS.read_text(encoding="utf-8")
    for token in ("--ap-needs", "--ap-blocked", "--ap-done"):
        assert token in css
    needs = re.search(r"--ap-needs:\s*(#[0-9a-fA-F]{6})", css).group(1).lower()
    gold = re.search(r"--ap-gold:\s*(#[0-9a-fA-F]{6})", css).group(1).lower()
    assert needs != gold


def test_wide_content_scrolls_inside_its_own_box():
    """The page body must not scroll horizontally on a phone."""
    css = CSS.read_text(encoding="utf-8")
    wrap = css[css.index(".ap-table-wrap"):]
    assert "overflow-x: auto" in wrap[:300]


def test_the_mobile_breakpoint_matches_the_app():
    """880px, per the rest of the product."""
    assert "max-width: 880px" in CSS.read_text(encoding="utf-8")


def test_the_mirrors_agree():
    """
    static/ is the source, firebase_public/ is what Hosting serves. Editing one
    changes nothing in production.
    """
    served = ROOT / "firebase_public" / "static" / "autofill_packs.css"
    assert served.exists(), "the stylesheet was never synced to firebase_public"
    assert served.read_text(encoding="utf-8") == CSS.read_text(encoding="utf-8")
