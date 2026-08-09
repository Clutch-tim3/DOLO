"""
The Agent-facing surface for Agent Autofill.

Four tools, shaped like `quotation_tools` so `tool_dispatch` can register them
without special cases:

    autofill_prepare_tender     fill the form, assess the tender, open a review
    autofill_review_status      what is filled, what is still flagged
    autofill_acknowledge_field  acknowledge ONE flagged field
    autofill_export_document    export — refuses "reviewed" while flags are open

`autofill_prepare_tender` is the "both results together" call: it returns the
filled draft AND the eligibility / win-probability verdict for the same file, so
the user does not have to upload the tender twice to learn that they are
disqualified from a form the agent just spent effort filling.

The fill itself is NOT done here. It is delegated to
`main_autofill_orchestrator.run_autofill`, which owns the tier check, the daily
quota and the classification call, in that order. Calling `fill_docx` directly
from this module would work and would be shorter, and it would also be a second
door into the fill engine that skips the quota gate entirely — a tier limit that
one code path enforces is not a tier limit. So if the orchestrator cannot be
imported, this tool returns the tender assessment (which costs no API call and
no quota) and reports that drafting is unavailable, rather than quietly filling
the form anyway.

What this module adds on top of the orchestrator is the part the orchestrator
deliberately stops short of: a persisted review whose flagged fields must be
acknowledged one at a time, and an export that refuses until they have been.
`AutofillRun.reviewed` and `.exportable` are hardcoded False there and have no
setter; `review_gate` is where they become true, and only through the database.
"""

from __future__ import annotations

from pathlib import Path

from agent.file_paths import public_url
from agent.memory.company_store import get_company_profile
from agent_autofill.integration.review_gate import (
    ReviewGateError,
    acknowledge_field,
    export_draft,
    export_reviewed,
    get_review,
    open_review,
)
from agent_autofill.integration.tender_assessment import assess_tender


def _run_autofill(company_id: str, path: Path):
    """
    Hand the document to the orchestrator. Returns (run, error_message).

    The import is deferred and guarded because the orchestrator is a sibling
    under active development: an ImportError here must cost the drafting half of
    one tool call, not the ability to load the agent's tool registry.
    """
    try:
        from agent_autofill.main_autofill_orchestrator import run_autofill
    except Exception as e:
        return None, (
            "Drafting is unavailable right now — the autofill pipeline could not "
            f"be loaded ({type(e).__name__}: {e}). Nothing was filled. The tender "
            "assessment below is unaffected."
        )
    try:
        return run_autofill(company_id, path), None
    except Exception as e:
        return None, f"The autofill pipeline failed on this document: {type(e).__name__}: {e}"


def prepare_tender(company_id: str, tender_file_path: str) -> dict:
    """
    Draft the form, assess the tender, and open a review over the result.

    The source document is never modified — `fill_docx` copies first and writes
    to the copy — and the copy lands in the generated directory so it downloads
    through the same route as every other generated artefact.
    """
    path = Path(tender_file_path)
    if not path.exists():
        return {"status": "error", "message": f"Document not found: {tender_file_path}"}

    profile = get_company_profile(company_id) or {}
    company_name = profile.get("company_name", "")

    # Assessment first, and unconditionally: it makes no Claude call and burns
    # no autofill quota, so a company that is out of drafts still gets told
    # whether the tender is worth pursuing.
    assessment = assess_tender(path, company_profile=profile)

    run, run_error = _run_autofill(company_id, path)

    review = None
    fill: dict = {"draft_path": None, "message": run_error} if run_error else {}

    if run is not None:
        fill = {
            "status": run.status,
            "message": run.message,
            "tier": run.tier,
            "quota": run.quota,
            "quota_consumed": run.quota_consumed,
            "classification": run.to_dict().get("classification"),
            "extraction": run.extraction_summary,
            "draft_path": run.output_document,
            "draft_url": (
                public_url(Path(run.output_document).name) if run.output_document else None
            ),
            "review_summary_url": run.review_summary_url,
        }

        if run.produced_draft and run.fill_result is not None:
            result = run.fill_result
            review = open_review(
                company_id, result,
                company_name=company_name,
                summary_path=run.review_summary_path or "",
            )
            fill.update({
                "summary_line": result.summary_line,
                "document_context": sorted(result.context),
                "filled": [
                    {"label": f.label, "value": f.value, "source": f.source,
                     "location": f.location, "low_confidence": f.low_confidence}
                    for f in result.filled
                ],
                "flagged": [
                    {"item_key": f"F{i:02d}", "label": s.label, "category": s.category,
                     "reason": s.reason, "location": s.location}
                    for i, s in enumerate(result.skipped, start=1)
                ],
            })

    return {
        "status": "success",
        "review_id": review["review_id"] if review else None,
        "assessment": assessment,
        "fill": fill,
        "review": review,
        # Restated on every response rather than assumed from the tool name,
        # because this string is what the model reads back before it writes a
        # sentence like "your form is ready to submit".
        "drafts_only": (
            "Everything produced here is a draft. No signature was applied, no "
            "price was written, and no declaration was answered. The document "
            "cannot be exported as reviewed until every flagged field is "
            "acknowledged one at a time."
        ),
    }


# --- handlers ---------------------------------------------------------------
# Each returns a dict, and each turns a ReviewGateError into the quotation
# module's refusal shape rather than raising: the agent loop needs a
# tool_result for every tool_use, and a refusal it can read beats a traceback.


def _guard(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except ReviewGateError as e:
        return {"status": "error", "message": str(e)}


def _autofill_prepare_tender(company_id: str, tender_file_path: str):
    return _guard(prepare_tender, company_id, tender_file_path)


def _autofill_review_status(company_id: str, review_id: str):
    return _guard(get_review, company_id, review_id)


def _autofill_show_filled_values(company_id: str, review_id: str):
    from agent_autofill.integration.review_gate import filled_values

    return filled_values(company_id, review_id)


def _autofill_confirm_filled_values(company_id: str, review_id: str,
                                    confirmed_keys: list = None):
    from agent_autofill.integration.review_gate import confirm_filled_values

    return confirm_filled_values(company_id, review_id, confirmed_keys)


def _autofill_acknowledge_field(company_id: str, review_id: str, item_key: str,
                                note: str = ""):
    return _guard(acknowledge_field, company_id, review_id, item_key, note)


def _autofill_export_document(company_id: str, review_id: str,
                              as_reviewed: bool = False):
    if as_reviewed:
        result = _guard(export_reviewed, company_id, review_id)
    else:
        result = _guard(export_draft, company_id, review_id)
    # main_agent surfaces `pdf_url` from any tool result, which is what puts a
    # download button in the chat. The generated file is a .docx, so the name is
    # sent alongside rather than letting the client guess.
    # Verify what we just produced, before handing anyone a link to it. This
    # is cheap and it is the only moment where a failure is unambiguously a bug
    # in us rather than tampering: nothing else has touched the file yet. It
    # also means verify_export is actually exercised in production, which it
    # was not — it had no caller outside the test suite.
    if isinstance(result, dict) and result.get("status") == "success" \
            and result.get("export_path") and as_reviewed:
        from agent_autofill.integration.review_gate import verify_export

        verdict = _guard(verify_export, result["export_path"], company_id, review_id)
        if not (isinstance(verdict, dict) and verdict.get("mac_verified")):
            detail = (verdict or {}).get("mac_detail", "") if isinstance(verdict, dict) else ""
            return {
                "status": "error",
                "message": (
                    "The export was written but does not verify against its own "
                    "review record, so it is not being offered for download. "
                    "This is a fault in CairoAI, not something you did. "
                    + str(detail)
                ).strip(),
                "export_path": result["export_path"],
            }
        result["verified"] = True

    if isinstance(result, dict) and result.get("download_url"):
        result["pdf_url"] = result["download_url"]
        result["download_name"] = Path(result["export_path"]).name
    return result


AUTOFILL_TOOL_HANDLERS = {
    "autofill_prepare_tender": _autofill_prepare_tender,
    "autofill_review_status": _autofill_review_status,
    "autofill_show_filled_values": _autofill_show_filled_values,
    "autofill_confirm_filled_values": _autofill_confirm_filled_values,
    "autofill_acknowledge_field": _autofill_acknowledge_field,
    "autofill_export_document": _autofill_export_document,
}


autofill_tools = [
    {
        "name": "autofill_prepare_tender",
        "description": (
            "Pre-fill a tender form from the company profile AND check eligibility "
            "and win probability for the same document. Produces a DRAFT only: no "
            "signature, no pricing, no declaration is ever answered. Returns a "
            "review_id, the fields filled, the fields flagged for the user, and the "
            "eligibility / win-probability verdict."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "tender_file_path": {"type": "string"},
            },
            "required": ["company_id", "tender_file_path"],
        },
    },
    {
        "name": "autofill_review_status",
        "description": (
            "Show the state of an autofill review: what was filled, which flagged "
            "fields have been acknowledged, and which are still outstanding."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "review_id": {"type": "string"},
            },
            "required": ["company_id", "review_id"],
        },
    },
    {
        "name": "autofill_acknowledge_field",
        "description": (
            "Acknowledge EXACTLY ONE flagged field, by its key (F01, F02, ...), with "
            "a note from the user saying what they checked or will do. Call once per "
            "field. There is no way to acknowledge several fields or all fields in "
            "one call, and a generic note such as 'ok' is rejected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "review_id": {"type": "string"},
                "item_key": {"type": "string", "description": "One field key, e.g. 'F03'."},
                "note": {
                    "type": "string",
                    "description": "The user's own words about this specific field.",
                },
            },
            "required": ["company_id", "review_id", "item_key", "note"],
        },
    },
    {
        "name": "autofill_show_filled_values",
        "description": (
            "List every value CairoAI pre-filled into the document, with its "
            "label. Show ALL of these to the user verbatim before confirming "
            "them — the confirmation records what they were shown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "review_id": {"type": "string"},
            },
            "required": ["company_id", "review_id"],
        },
    },
    {
        "name": "autofill_confirm_filled_values",
        "description": (
            "Record that the user has confirmed the pre-filled values, having "
            "seen them. Pass every item_key from autofill_show_filled_values — "
            "a partial set is refused, because a partial confirmation is not a "
            "review. Only call this after the user has actually said the values "
            "are correct. Never call it on their behalf to move things along."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "review_id": {"type": "string"},
                "confirmed_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Every item_key shown to the user.",
                },
            },
            "required": ["company_id", "review_id", "confirmed_keys"],
        },
    },
    {
        "name": "autofill_export_document",
        "description": (
            "Export the filled document. With as_reviewed=false it exports an "
            "UNREVIEWED draft, always allowed and clearly marked as incomplete "
            "inside the file. With as_reviewed=true it WILL FAIL unless the "
            "pre-filled values have been confirmed AND every flagged field has "
            "been acknowledged individually first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "review_id": {"type": "string"},
                "as_reviewed": {
                    "type": "boolean",
                    "description": "True only after every flagged field is acknowledged.",
                },
            },
            "required": ["company_id", "review_id"],
        },
    },
]
