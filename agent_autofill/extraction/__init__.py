"""Document understanding for Agent Autofill.

Three layers, used in order of decreasing reliability:

1. ``acroform_extractor``      — real PDF form fields (AcroForm). Authoritative
                                 when present, which for SA tender documents is
                                 rare.
2. ``layout_blank_extractor``  — geometric blank detection for unstructured PDFs
                                 (the common case) and for DOCX tables.
3. ``field_alias_dictionary``  — fuzzy mapping from a detected label to a
                                 canonical field name.

Nothing here fills anything in. It reports where the blanks are, what label sits
next to each one, and how confident it is — including an explicit "I do not
know" for labels it cannot map safely.
"""

from agent_autofill.extraction.acroform_extractor import (
    AcroFormField,
    AcroFormResult,
    extract_acroform,
    has_acroform,
)
from agent_autofill.extraction.field_alias_dictionary import (
    CANONICAL_FIELDS,
    AliasMatch,
    match_label,
    normalize_label,
)
from agent_autofill.extraction.layout_blank_extractor import (
    Blank,
    extract_docx_blanks,
    extract_pdf_blanks,
)
from agent_autofill.extraction.pipeline import ExtractionReport, extract_document

__all__ = [
    "AcroFormField",
    "AcroFormResult",
    "AliasMatch",
    "Blank",
    "CANONICAL_FIELDS",
    "ExtractionReport",
    "extract_acroform",
    "extract_docx_blanks",
    "extract_document",
    "extract_pdf_blanks",
    "has_acroform",
    "match_label",
    "normalize_label",
]
