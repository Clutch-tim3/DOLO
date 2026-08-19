"""
One brand colour in, a whole document palette out.

WHY THIS EXISTS
---------------
Every company that uses CairoAI gets the same quotation layout with its own
identity on it. Asking a supplier to nominate five colours is asking them to be
a designer; asking for one is asking their brand colour, which they already
know. Everything else is derived here.

The reference document a real quotation was modelled on used:

    #40220a   dark brown   header row, right spine, footer band
    #d9d0c1   warm grey    rules and borders (18 separate uses)
    #b39666   tan          the tagline under the wordmark
    #f3eee3   cream        the tint behind the total row

Those four are not four decisions. They are one colour seen at four
lightnesses, which is what `palette()` reproduces for any input — so a company
whose colour is navy, burgundy or olive gets a document that hangs together the
same way, rather than brown furniture with a navy hat.

WHY OLIVE IS THE DEFAULT
------------------------
A quotation must never look unfinished. A company that has not chosen a colour
still needs a document that reads as deliberate, so the fallback is a considered
dark olive rather than black or a neutral grey — warm enough to sit with the
cream tints, sober enough for a government buyer.
"""

from __future__ import annotations

import colorsys
import re

#: The fallback, used whenever a company has not set a brand colour. A dark,
#: desaturated olive: it reads as a choice rather than an absence.
DEFAULT_BRAND = "#3E4A21"

#: Text drawn on top of a band of the brand colour.
ON_BRAND_TEXT = "#FFFFFF"

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _to_rgb(value: str) -> tuple[float, float, float]:
    match = _HEX.match((value or "").strip())
    if not match:
        match = _HEX.match(DEFAULT_BRAND)
    raw = match.group(1)
    return tuple(int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def _shift(rgb, *, lightness=None, saturation=None):
    """Move a colour in HLS space, which keeps its hue intact."""
    h, l, s = colorsys.rgb_to_hls(*rgb)
    if lightness is not None:
        l = max(0.0, min(1.0, lightness))
    if saturation is not None:
        s = max(0.0, min(1.0, saturation))
    return colorsys.hls_to_rgb(h, l, s)


def normalise(value: str | None) -> str:
    """A usable brand colour, whatever the company did or did not provide."""
    if not value:
        return DEFAULT_BRAND
    match = _HEX.match(str(value).strip())
    return f"#{match.group(1).lower()}" if match else DEFAULT_BRAND


def palette(brand: str | None = None) -> dict[str, str]:
    """
    The five colours a quotation needs, derived from one.

    `band` is deliberately darkened rather than used raw. A supplier's brand
    colour is often chosen for a screen — a bright teal or a mid red — and a
    full-width band of it behind white text is unreadable when printed and
    looks like a warning label. Clamping the lightness keeps the hue they chose
    while producing a document a procurement officer will not wince at.
    """
    rgb = _to_rgb(normalise(brand))
    _, lightness, saturation = colorsys.rgb_to_hls(*rgb)

    band = _shift(rgb, lightness=min(lightness, 0.22),
                  saturation=min(saturation, 0.75))
    # Mid-tone for the tagline and small emphasis. Light enough to read on
    # white, dark enough not to disappear on a photocopy.
    accent = _shift(rgb, lightness=0.42, saturation=min(saturation, 0.55))
    # Rules and borders: the hue is present but almost spent, so a page full of
    # them reads as warm grey rather than as colour.
    rule = _shift(rgb, lightness=0.82, saturation=0.14)
    # The tint behind a total row, and any other filled cell.
    tint = _shift(rgb, lightness=0.945, saturation=0.22)

    return {
        "band": _to_hex(band),
        "accent": _to_hex(accent),
        "rule": _to_hex(rule),
        "tint": _to_hex(tint),
        "on_band": ON_BRAND_TEXT,
    }
