"""
The spend gate.

Everything downstream of Agent Autofill — extraction, the fill engine, the
review summary — is free. The classification call is not, and a connected
folder will contain far more non-tenders than tenders: invoices, certificates,
photographs, the company's own policy documents.

This package answers one question as cheaply as possible: *is this a tender
document at all?* It refuses on file type before reading anything, reads only
the first page or so, and asks Haiku rather than the chat model. A "no" here
saves every later cost.
"""

from agent_autofill.classification.is_tender_document import (
    CONFIDENCE_THRESHOLD,
    CLASSIFIER_MODEL,
    SUPPORTED_SUFFIXES,
    ClassificationResult,
    classify_document,
    extract_text_head,
    is_classifiable,
)

__all__ = [
    "CLASSIFIER_MODEL",
    "CONFIDENCE_THRESHOLD",
    "ClassificationResult",
    "SUPPORTED_SUFFIXES",
    "classify_document",
    "extract_text_head",
    "is_classifiable",
]
