"""Detect and read a PDF's AcroForm (real, interactive form fields).

When a tender pack ships with a genuine AcroForm, this is the only extractor
worth trusting: the field names, types and rectangles are authored data, not
inferred geometry. Every field has an unambiguous target, so the fill engine can
write values directly.

In practice SA tender packs are overwhelmingly flat prints — scanned, or
exported from Word without form controls — so ``has_acroform`` returning False
is the normal path, and ``layout_blank_extractor`` takes over.

Caveats worth knowing:

* A PDF can carry an ``/AcroForm`` dictionary with an empty ``/Fields`` array.
  That is *not* a usable form. ``AcroFormResult.is_fillable`` accounts for this;
  the raw ``has_acroform`` flag does not.
* ``/FT`` (field type) is inheritable from a parent field node, so it is
  resolved by walking up ``/Parent`` rather than read off the leaf only.
* Radio groups and checkbox kids share one parent field name; each kid gets its
  own widget rectangle. They are reported per widget with the shared name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.generic import IndirectObject

# /Ff bit flags we care about (PDF 32000-1 table 226/228).
_FF_READ_ONLY = 1 << 0
_FF_REQUIRED = 1 << 1
_FF_MULTILINE = 1 << 12
_FF_RADIO = 1 << 15

_FIELD_TYPE_NAMES = {
    "/Tx": "text",
    "/Btn": "button",
    "/Ch": "choice",
    "/Sig": "signature",
}


@dataclass(frozen=True)
class AcroFormField:
    """One interactive form field (one widget annotation)."""

    name: str
    field_type: str  # text | button | choice | signature | unknown
    raw_type: str | None  # the literal /FT value
    page_number: int | None  # 0-based; None if the widget is not on a page
    rect: tuple[float, float, float, float] | None  # (x0, y0, x1, y1) PDF space
    value: str | None
    default_value: str | None
    is_required: bool = False
    is_read_only: bool = False
    is_multiline: bool = False
    is_radio: bool = False
    alternate_name: str | None = None  # /TU — the tooltip, often the human label
    choices: tuple[str, ...] = ()

    @property
    def label_candidate(self) -> str:
        """Best human-readable label for this field.

        ``/TU`` is the accessibility/tooltip text and is usually a real sentence
        ("Name of Bidder"); ``/T`` is often a machine name ("txtField12"). Prefer
        the former when it exists.
        """
        return self.alternate_name or self.name


@dataclass
class AcroFormResult:
    path: str
    has_acroform: bool
    fields: list[AcroFormField] = field(default_factory=list)
    error: str | None = None
    needs_appearances: bool = False

    @property
    def is_fillable(self) -> bool:
        """True only if there is an AcroForm *and* it actually has fields."""
        return self.has_acroform and bool(self.fields)

    @property
    def field_count(self) -> int:
        return len(self.fields)


def _resolve(obj: Any) -> Any:
    return obj.get_object() if isinstance(obj, IndirectObject) else obj


def _inherited(node: Any, key: str, depth: int = 0) -> Any:
    """Read ``key`` off a field node, walking /Parent for inheritable keys."""
    seen = 0
    while node is not None and seen <= 32:
        node = _resolve(node)
        if not hasattr(node, "get"):
            return None
        if key in node:
            return _resolve(node[key])
        node = node.get("/Parent")
        seen += 1
    return None


def _full_name(node: Any) -> str:
    """Fully-qualified field name: parent.child, per PDF spec 12.7.3.2."""
    parts: list[str] = []
    seen = 0
    while node is not None and seen <= 32:
        node = _resolve(node)
        if not hasattr(node, "get"):
            break
        partial = node.get("/T")
        if partial is not None:
            parts.append(str(_resolve(partial)))
        node = node.get("/Parent")
        seen += 1
    return ".".join(reversed(parts))


def has_acroform(path: str | Path) -> bool:
    """Cheap check: does the document declare an /AcroForm at all?

    Note this is intentionally permissive — it answers the structural question.
    Use ``extract_acroform(...).is_fillable`` to know whether the form is
    actually usable.
    """
    try:
        reader = PdfReader(str(path))
        root = reader.trailer.get("/Root")
        if root is None:
            return False
        return "/AcroForm" in _resolve(root)
    except Exception:
        return False


def extract_acroform(path: str | Path) -> AcroFormResult:
    """Read every AcroForm field, with page index and widget rectangle."""
    path_str = str(path)
    try:
        reader = PdfReader(path_str)
    except Exception as exc:  # malformed / not a PDF at all
        return AcroFormResult(path_str, False, error=f"{type(exc).__name__}: {exc}")

    try:
        root = _resolve(reader.trailer.get("/Root")) or {}
        acro = root.get("/AcroForm")
    except Exception as exc:
        return AcroFormResult(path_str, False, error=f"{type(exc).__name__}: {exc}")

    if acro is None:
        return AcroFormResult(path_str, False)

    acro = _resolve(acro)
    needs_appearances = bool(_resolve(acro.get("/NeedAppearances")) or False)

    # Map every widget's indirect reference to its page index, so each field can
    # report where it lives. Fields with no widget (rare) get page None.
    widget_page: dict[int, int] = {}
    for page_index, page in enumerate(reader.pages):
        try:
            annots = page.get("/Annots")
        except Exception:
            continue
        if annots is None:
            continue
        for annot in _resolve(annots) or []:
            if isinstance(annot, IndirectObject):
                widget_page[annot.idnum] = page_index

    fields: list[AcroFormField] = []
    errors: list[str] = []

    try:
        raw_fields = _resolve(acro.get("/Fields")) or []
    except Exception as exc:
        return AcroFormResult(
            path_str, True, error=f"unreadable /Fields: {type(exc).__name__}: {exc}"
        )

    # Iterative walk of the field tree; /Kids can nest arbitrarily.
    stack: list[tuple[Any, Any]] = [(ref, None) for ref in raw_fields]
    visited: set[int] = set()

    while stack:
        ref, _parent = stack.pop(0)
        idnum = ref.idnum if isinstance(ref, IndirectObject) else None
        if idnum is not None:
            if idnum in visited:
                continue
            visited.add(idnum)

        try:
            node = _resolve(ref)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        if not hasattr(node, "get"):
            continue

        kids = _resolve(node.get("/Kids"))
        # A node with /Kids that are *fields* (they have /T) is an intermediate
        # node. Kids without /T are widget annotations of this same field.
        if kids:
            kid_objs = [(k, ref) for k in kids]
            kids_are_fields = False
            for k in kids:
                ko = _resolve(k)
                if hasattr(ko, "get") and "/T" in ko:
                    kids_are_fields = True
                    break
            if kids_are_fields:
                stack.extend(kid_objs)
                continue

        raw_ft = _inherited(node, "/FT")
        raw_ft_str = str(raw_ft) if raw_ft is not None else None
        try:
            flags = int(_inherited(node, "/Ff") or 0)
        except Exception:
            flags = 0

        value = _inherited(node, "/V")
        default = _inherited(node, "/DV")

        opts_raw = _inherited(node, "/Opt")
        choices: tuple[str, ...] = ()
        if opts_raw:
            collected = []
            for opt in opts_raw:
                opt = _resolve(opt)
                if isinstance(opt, (list, tuple)) and opt:
                    collected.append(str(_resolve(opt[-1])))
                else:
                    collected.append(str(opt))
            choices = tuple(collected)

        alt = _inherited(node, "/TU")
        name = _full_name(node) or "(unnamed)"

        # Widgets: either this node is itself a widget, or its kids are.
        widget_nodes: list[Any] = []
        if kids:
            widget_nodes = list(kids)
        else:
            widget_nodes = [ref]

        for widget_ref in widget_nodes:
            widget = _resolve(widget_ref)
            rect: tuple[float, float, float, float] | None = None
            if hasattr(widget, "get") and widget.get("/Rect") is not None:
                try:
                    raw_rect = [float(_resolve(v)) for v in _resolve(widget["/Rect"])]
                    x0, y0, x1, y1 = raw_rect
                    rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                except Exception:
                    rect = None

            page_number = None
            if isinstance(widget_ref, IndirectObject):
                page_number = widget_page.get(widget_ref.idnum)
            if page_number is None and isinstance(ref, IndirectObject):
                page_number = widget_page.get(ref.idnum)

            fields.append(
                AcroFormField(
                    name=name,
                    field_type=_FIELD_TYPE_NAMES.get(raw_ft_str or "", "unknown"),
                    raw_type=raw_ft_str,
                    page_number=page_number,
                    rect=rect,
                    value=None if value is None else str(value),
                    default_value=None if default is None else str(default),
                    is_required=bool(flags & _FF_REQUIRED),
                    is_read_only=bool(flags & _FF_READ_ONLY),
                    is_multiline=bool(flags & _FF_MULTILINE),
                    is_radio=bool(flags & _FF_RADIO),
                    alternate_name=None if alt is None else str(alt),
                    choices=choices,
                )
            )

    return AcroFormResult(
        path_str,
        True,
        fields=fields,
        error="; ".join(errors) if errors else None,
        needs_appearances=needs_appearances,
    )
