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


def _autofill_missing_details(company_id: str, review_id: str):
    """
    What this pack still needs from the user, as questions rather than flags.

    P0-2. Reported per PROFILE FIELD, not per blank: on the owner's pack 24
    outstanding fields collapse to 11 questions, because "Designation" and
    "Capacity" appear fourteen times between them and are one fact.
    """
    from agent.memory.company_store import get_company_profile
    from agent_autofill.integration.missing_fields import missing_profile_fields
    # `_askable_rows`, not `_outstanding_rows`: the second filters out advisory
    # items, and "ADDRESS — too general to answer safely" is advisory. It must
    # not block an export, and it absolutely must be asked about.
    from agent_autofill.integration.review_gate import _askable_rows, _load_review

    def _collect(cid, rid):
        _load_review(cid, rid)  # tenant pin
        rows = _askable_rows(rid)
        missing = missing_profile_fields(rows, get_company_profile(cid) or {})
        return {
            "status": "success",
            "review_id": rid,
            "questions": missing,
            "question_count": len(missing),
            "fields_waiting": sum(m["count"] for m in missing),
            "message": (
                f"{len(missing)} thing(s) to ask about, covering "
                f"{sum(m['count'] for m in missing)} blank(s)."
                if missing else
                "Nothing outstanding that the user can answer."
            ),
        }

    return _guard(_collect, company_id, review_id)


def _autofill_resolve_label(company_id: str, label: str, field: str):
    """
    Record which field a too-general label means, after the USER has said so.

    The other half of a "which_one" question. When the answer to "page 3 just
    says ADDRESS — physical or postal?" is "physical", there is nothing to write
    to the profile: both addresses are already on file. What was missing is the
    MAPPING, and without somewhere to put it the same question gets asked on the
    next pack, and the one after that.

    This writes it to `learned_labels`, scoped to this company, which is where
    the fill engine already looks when the dictionary cannot place a label. So
    the answer fills this pack on the next refill and every pack after it.

    IT CANNOT UNBLOCK ANYTHING. The field goes through `never_fill_fields.
    is_blocked` and `SAFE_FILL_FIELDS` in the fill engine exactly as a
    dictionary match does. Teaching that "Signature" means company_name records
    a lesson that is then never consulted, because the blocklist runs on the
    label first. A wrong answer here costs a wrong value in a fillable field,
    visible in the review and correctable with the same tool.
    """
    from agent_autofill.extraction import learned_labels
    from agent_autofill.fill_engine.safe_fill_fields import SAFE_FILL_FIELDS

    def _teach(cid, lbl, fld):
        if fld not in SAFE_FILL_FIELDS:
            return {
                "status": "error",
                "message": (
                    f"'{fld}' is not a field CairoAI fills. Valid fields: "
                    + ", ".join(sorted(SAFE_FILL_FIELDS)) + "."
                ),
            }
        learned_labels.teach(cid, lbl, canonical_field=fld, taught_by="user")
        return {
            "status": "success",
            "label": lbl,
            "field": fld,
            "message": (
                f"Noted: on this company's forms, '{lbl}' means {fld}. It will "
                f"fill from now on. Call autofill_refill to apply it to this pack."
            ),
        }

    return _guard(_teach, company_id, label, field)


def _autofill_compliance_check(company_id: str, review_id: str):
    """
    What would get this bid thrown out, before anyone reads the proposal.

    `SBD_COMPLIANCE.md`: administrative mistakes disqualify more South African
    submissions than weak pricing does, and CairoAI is the only party that sees
    the whole pack, the profile and the vault at once.

    It reports. `export_reviewed` remains the only thing that refuses an export.
    """
    from agent.memory.company_store import get_company_documents, get_company_profile
    from agent_autofill.fill_engine.preference_goals import (
        find_goals_table, goal_rows, propose_claims,
    )
    from agent_autofill.integration.compliance_checks import (
        disqualification_summary, find_closing_date,
    )
    from agent_autofill.integration.review_gate import _load_review

    def _check(cid, rid):
        review = _load_review(cid, rid)  # tenant pin
        source = Path(review["source_path"]) if review["source_path"] else None
        profile = get_company_profile(cid) or {}

        closing, proposals = None, []
        if source and source.exists() and source.suffix.lower() == ".pdf":
            import fitz
            import pdfplumber

            with fitz.open(str(source)) as doc:
                closing = find_closing_date(
                    "\n".join(page.get_text() for page in doc))

            with pdfplumber.open(str(source)) as pdf:
                for page in pdf.pages:
                    found = find_goals_table(page)
                    if found:
                        proposals = propose_claims(goal_rows(found), profile)
                        break

        # The stored draft's own record of what it filled and refused. Rebuilt
        # from the review rather than re-filling: re-filling would produce a
        # different document from the one the user is looking at.
        from agent_autofill.integration.review_gate import _askable_rows

        class _Row:
            def __init__(self, row):
                self.label = row.get("label") or ""
                self.reason = row.get("reason") or ""
                self.location = row.get("location") or ""
                self.value = None
                self.canonical_field = None

        result = type("R", (), {
            "filled": [],
            "skipped": [_Row(r) for r in _askable_rows(rid)],
        })()

        summary = disqualification_summary(
            result, profile, closing=closing,
            documents=get_company_documents(cid) or [],
            goal_proposals=proposals)
        summary["status"] = "success"
        summary["review_id"] = rid
        summary["preference_goals"] = proposals
        return summary

    return _guard(_check, company_id, review_id)


def _autofill_refill(company_id: str, review_id: str):
    """
    Re-run the fill for a review after the profile has been updated.

    So a user who answers a question does not upload the pack again. The
    document is re-filled from the SAME source and the review is rebuilt, which
    means every acknowledgement is made afresh — a value that changed has not
    been reviewed yet, and carrying old acknowledgements across would be
    acknowledging something nobody saw.
    """
    from agent_autofill.integration.review_gate import refill_review

    return _guard(refill_review, company_id, review_id)


AUTOFILL_TOOL_HANDLERS = {
    "autofill_prepare_tender": _autofill_prepare_tender,
    "autofill_review_status": _autofill_review_status,
    "autofill_show_filled_values": _autofill_show_filled_values,
    "autofill_confirm_filled_values": _autofill_confirm_filled_values,
    "autofill_acknowledge_field": _autofill_acknowledge_field,
    "autofill_export_document": _autofill_export_document,
    "autofill_missing_details": _autofill_missing_details,
    "autofill_resolve_label": _autofill_resolve_label,
    "autofill_compliance_check": _autofill_compliance_check,
    "autofill_refill": _autofill_refill,
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
        "name": "autofill_missing_details",
        "description": (
            "What this pack still needs from the user, as questions. Call it after "
            "processing a pack and ASK the user for what it returns, in your own "
            "words, rather than telling them to go and acknowledge flags. Each "
            "entry is one profile field with a `prompt` saying what to ask for, a "
            "`count` of how many blanks it would fill, and the `asked_by` labels "
            "showing where the form asks for it. Fields marked `personal` are "
            "someone's own details — ask for them plainly and never guess. "
            "When the user answers, write it with update_company_profile using "
            "confirmed=true ONLY after showing them the exact value you are about "
            "to save, then call autofill_refill."
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
        "name": "autofill_resolve_label",
        "description": (
            "Record which profile field a too-general form label means, AFTER the "
            "user has told you. This is how you answer a `which_one` question from "
            "autofill_missing_details: a blank labelled only 'ADDRESS' could be "
            "postal or physical, so you ask, and then you call this with what they "
            "said. Do NOT call it on your own judgement, on a guess, or because the "
            "profile only holds one of the two — the user's answer is the only "
            "input. The lesson is remembered for this company, so the question is "
            "asked once and never again. Call autofill_refill afterwards to apply "
            "it. It cannot make CairoAI fill a signature, a price or a declaration: "
            "those are refused on the label before any lesson is consulted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "label": {
                    "type": "string",
                    "description": "The form label exactly as it appears, e.g. 'ADDRESS'.",
                },
                "field": {
                    "type": "string",
                    "description": (
                        "The profile field the user said it means, e.g. "
                        "'physical_address' or 'postal_address'."
                    ),
                },
            },
            "required": ["company_id", "label", "field"],
        },
    },
    {
        "name": "autofill_compliance_check",
        "description": (
            "What would get this bid thrown out on administrative grounds — run "
            "it after a pack is filled and BEFORE offering an export, and lead "
            "your reply with what it returns. Administrative mistakes disqualify "
            "more South African submissions than weak pricing does, and CairoAI "
            "is the only party that sees the whole pack, the profile and the "
            "vault at once. It reports: signature lines still to sign and their "
            "pages, the same detail written two different ways across forms, "
            "certificates that expire before the tender closes, and the SBD 6.1 "
            "specific goals with what this tender allocates for each. It does "
            "NOT block anything — only autofill_export_document refuses. Read "
            "`would_disqualify` out plainly; a user who reads nothing else "
            "should still learn which signatures they have to add. For each "
            "entry in `preference_goals` with action 'ask', ask the user that "
            "question — never claim a goal they have not confirmed."
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
        "name": "autofill_refill",
        "description": (
            "Fill the same document again from the updated company profile, so the "
            "user does not upload it a second time. Returns a NEW review_id: the "
            "draft has changed, so it must be reviewed from the start, and the "
            "previous review is kept as the record of what was reviewed before. "
            "Use the new review_id for everything afterwards. Refused once a "
            "document has been exported."
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
