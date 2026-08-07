"""Tie the three extraction layers into one report.

Order of preference:
  1. AcroForm, when the PDF has one with actual fields — authored data beats
     inferred geometry every time.
  2. Layout blank detection otherwise (the normal case for SA tender packs).

Then every detected label is run through the alias dictionary. A blank whose
label cannot be mapped confidently is kept in the report as unmatched rather
than dropped — the caller needs to see what was found and not understood.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_autofill.extraction.acroform_extractor import AcroFormResult, extract_acroform
from agent_autofill.extraction.field_alias_dictionary import AliasMatch, match_label
from agent_autofill.extraction.layout_blank_extractor import (
    Blank,
    extract_docx_blanks,
    extract_pdf_blanks,
)


@dataclass
class DetectedField:
    """A blank plus the outcome of trying to map its label."""

    blank: Blank
    match: AliasMatch

    @property
    def canonical(self) -> str | None:
        return self.match.canonical

    @property
    def is_actionable(self) -> bool:
        """Safe for the fill engine to draft into."""
        return self.match.is_confident and self.blank.confidence >= 0.55


@dataclass
class ExtractionReport:
    path: str
    doc_type: str                 # "pdf" | "docx"
    strategy_used: str            # "acroform" | "layout"
    acroform: AcroFormResult | None
    fields: list[DetectedField] = field(default_factory=list)

    @property
    def matched(self) -> list[DetectedField]:
        return [f for f in self.fields if f.match.is_confident]

    @property
    def unmatched(self) -> list[DetectedField]:
        return [f for f in self.fields if not f.match.is_confident]

    @property
    def actionable(self) -> list[DetectedField]:
        return [f for f in self.fields if f.is_actionable]

    def by_status(self, status: str) -> list[DetectedField]:
        return [f for f in self.fields if f.match.status == status]

    def summary(self) -> dict[str, int]:
        counts = {
            "blanks_detected": len(self.fields),
            "matched": len(self.matched),
            "unmatched": len(self.unmatched),
            "actionable": len(self.actionable),
        }
        for status in ("exact", "fuzzy", "ambiguous", "blocked", "unmatched"):
            counts[f"status_{status}"] = len(self.by_status(status))
        return counts


def extract_document(
    path: str | Path,
    pages: list[int] | None = None,
    include_trailing_gaps: bool = False,
) -> ExtractionReport:
    """Run the full extraction pipeline over one PDF or DOCX."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".docx":
        blanks = extract_docx_blanks(path)
        return ExtractionReport(
            path=str(path),
            doc_type="docx",
            strategy_used="layout",
            acroform=None,
            fields=[DetectedField(b, match_label(b.label_text)) for b in blanks],
        )

    acro = extract_acroform(path)
    if acro.is_fillable:
        fields = [
            DetectedField(
                Blank(
                    source="pdf",
                    page_number=f.page_number if f.page_number is not None else -1,
                    bbox=None,  # AcroForm rects are bottom-up; not the layout convention
                    label_text=f.label_candidate,
                    label_bbox=None,
                    confidence=0.97,  # authored field, not inferred
                    strategy="acroform",
                    label_origin="acroform",
                    notes=(f"raw_type={f.raw_type}",),
                ),
                match_label(f.label_candidate),
            )
            for f in acro.fields
        ]
        return ExtractionReport(str(path), "pdf", "acroform", acro, fields)

    blanks = extract_pdf_blanks(path, pages=pages, include_trailing_gaps=include_trailing_gaps)
    return ExtractionReport(
        path=str(path),
        doc_type="pdf",
        strategy_used="layout",
        acroform=acro,
        fields=[DetectedField(b, match_label(b.label_text)) for b in blanks],
    )
