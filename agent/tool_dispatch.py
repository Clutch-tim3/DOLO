"""
Maps Claude `tool_use` requests onto the real Python functions, with the two
guards that a tool loop needs and a single-pass call did not:

  1. Tenant pinning. `company_id` in a tool's input is model-generated text.
     Whatever the model puts there is discarded and replaced with the
     session's company_id, so a prompt-injected document cannot make the
     agent read another tenant's profile.

  2. Path confinement. `file_path` / `tender_file_path` are also model-
     generated. They are resolved and required to sit inside an allowed
     upload root, so the agent cannot be talked into parsing /etc/passwd
     or any other file on the box.
"""

from pathlib import Path

from agent.memory.company_store import (
    get_company_profile,
    get_company_documents,
    update_company_profile,
    search_conversation_history,
)
from agent.navigation.app_help import get_app_help
from agent.onboarding.vet_company import vet_company_document

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Mirrors app.py: UPLOAD_FOLDER = DATA_DIR / "archive"
ALLOWED_FILE_ROOTS = [
    (PROJECT_ROOT / "data" / "archive").resolve(),
    (PROJECT_ROOT / "static" / "downloads").resolve(),
]

# Tool inputs whose value is always forced to the session's company_id.
TENANT_FIELDS = {"company_id"}

# Tool inputs that are treated as filesystem paths and confined.
PATH_FIELDS = {"file_path", "tender_file_path"}


class ToolExecutionError(Exception):
    """Raised for input the agent supplied that we refuse to act on."""


def _resolve_safe_path(raw: str) -> str:
    if not raw or not str(raw).strip():
        raise ToolExecutionError("No file path was supplied.")

    candidate = Path(str(raw))
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate

    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as e:
        raise ToolExecutionError(f"Could not resolve path: {e}") from e

    for root in ALLOWED_FILE_ROOTS:
        if resolved == root or root in resolved.parents:
            if not resolved.exists():
                raise ToolExecutionError(f"File not found: {resolved.name}")
            return str(resolved)

    raise ToolExecutionError(
        "That file is outside the uploads directory, so I can't open it. "
        "Upload the document through the app first."
    )


def _sanitize(tool_input: dict, company_id: str) -> dict:
    clean = dict(tool_input or {})
    for field in TENANT_FIELDS:
        if field in clean:
            clean[field] = company_id
    for field in PATH_FIELDS:
        if field in clean:
            clean[field] = _resolve_safe_path(clean[field])
    return clean


# --- handlers ---------------------------------------------------------------
# Thin wrappers so the registry can call everything with **kwargs and so the
# quote flows keep their existing multi-step behaviour.

def _generate_draft_quote(company_id: str, tender_file_path: str):
    # Imported here to avoid a circular import with main_agent.
    from agent.main_agent import generate_draft_quote_flow

    return generate_draft_quote_flow(company_id, tender_file_path)


def _finalize_quotation(quote_id: str, confirmed_items: list):
    from agent.main_agent import finalize_quote_flow

    return finalize_quote_flow(quote_id, confirmed_items)


# The five types the Compliance Vault treats as required. Kept in step with the
# doc_types list in app.py's /api/compliance-status.
REQUIRED_DOC_TYPES = [
    "tax_clearance",
    "bbbee_certificate",
    "cidb_grading",
    "csd_report",
    "cipc_registration",
]

# models/pdf_parser.classify_document_type() names blocks differently from the
# vault's required types, and only TAX_CLEARANCE happened to line up. Without
# this map an uploaded CSD report was stored as "csd_cert", which matches
# nothing, so four of the five required documents could never be satisfied and
# the vault stayed permanently incomplete.
CLASSIFIER_TO_VAULT_TYPE = {
    "CSD_CERT": "csd_report",
    "BBBEE_CERT": "bbbee_certificate",
    "TAX_CLEARANCE": "tax_clearance",
    "CIDB_CERT": "cidb_grading",
    "CIPC_COR14_3": "cipc_registration",
}


def vault_type_for(classifier_type: str) -> str:
    """Map a classifier block name onto the vault's document type."""
    if not classifier_type:
        return "unknown"
    return CLASSIFIER_TO_VAULT_TYPE.get(
        classifier_type.upper(), classifier_type.lower()
    )


DOC_TYPE_LABELS = {
    "tax_clearance": "Tax Clearance Certificate",
    "bbbee_certificate": "B-BBEE Certificate",
    "cidb_grading": "CIDB Grading",
    "csd_report": "CSD Report",
    "cipc_registration": "CIPC Registration",
}


def _get_vault_status(company_id: str):
    """
    Which required vault documents this company has, and what was parsed out of
    them. This is what tells the agent whether the vault is complete enough to
    vet, and it is also where the commodity/industry data for the accreditation
    recommendations comes from.
    """
    docs = get_company_documents(company_id)
    by_type = {}
    for d in docs:
        dtype = (d.get("document_type") or d.get("type") or "").lower()
        if dtype:
            by_type.setdefault(dtype, d)

    present, missing = [], []
    for dtype in REQUIRED_DOC_TYPES:
        (present if dtype in by_type else missing).append(
            {"type": dtype, "label": DOC_TYPE_LABELS.get(dtype, dtype)}
        )

    # Merge everything the parser pulled out of the uploaded documents, so the
    # agent sees industry/commodity codes without re-reading the PDFs.
    merged_fields = {}
    for d in docs:
        parsed = d.get("parsed_fields")
        if isinstance(parsed, dict):
            for k, v in parsed.items():
                if v not in (None, "", []):
                    merged_fields.setdefault(k, v)

    return {
        "complete": not missing,
        "present": present,
        "missing": missing,
        "required_count": len(REQUIRED_DOC_TYPES),
        "present_count": len(present),
        "parsed_company_facts": merged_fields,
    }


def _generate_accreditation_report(company_id: str, items: list):
    """
    Render the agent's accreditation recommendations to a PDF.

    The agent decides what to recommend and supplies the links; this only lays
    them out, so the advice stays tied to whatever the company's documents
    actually say rather than to a fixed table.
    """
    from agent.onboarding.accreditation_report import generate_accreditation_report

    if not isinstance(items, list) or not items:
        return {
            "status": "error",
            "message": "No accreditation items were supplied, so there is nothing to render.",
        }

    profile = get_company_profile(company_id) or {}
    # Parsed document facts fill the gaps the stored profile does not cover
    # (industry, CSD supplier number), so the report header reflects the vault.
    facts = _get_vault_status(company_id).get("parsed_company_facts", {})
    for key in ("industry", "supplier_number", "bbbee_level", "registration_number", "company_name"):
        if not profile.get(key) and facts.get(key):
            profile[key] = facts[key]

    return generate_accreditation_report(profile, items)


TOOL_REGISTRY = {
    "get_company_profile": lambda company_id: get_company_profile(company_id),
    "get_company_documents": lambda company_id: get_company_documents(company_id),
    # `confirmed` defaults to False, so a model that omits it gets a refusal and
    # a diff rather than a write. The gate itself lives in company_store; this
    # only forwards the flag.
    "update_company_profile": lambda company_id, fields, confirmed=False: update_company_profile(
        company_id, fields, confirmed=confirmed
    ),
    "search_conversation_history": lambda company_id, query=None: search_conversation_history(
        company_id, query
    ),
    "get_app_help": lambda feature_query: get_app_help(feature_query),
    "vet_company_document": lambda company_id, file_path, doc_type="UNKNOWN": vet_company_document(
        company_id, file_path, doc_type
    ),
    "generate_draft_quote": _generate_draft_quote,
    "finalize_quotation": _finalize_quotation,
    "get_vault_status": lambda company_id: _get_vault_status(company_id),
    "generate_accreditation_report": _generate_accreditation_report,
}

# Agent Autofill. Registered by update rather than inline so a failure inside
# agent_autofill (a half-built sibling module, a missing optional dependency)
# degrades to "those tools are unavailable" instead of taking down every tool
# the agent has, including the quotation flow. company_id is pinned by
# _sanitize for these exactly as it is for the rest.
try:
    from agent_autofill.integration.autofill_tools import AUTOFILL_TOOL_HANDLERS

    TOOL_REGISTRY.update(AUTOFILL_TOOL_HANDLERS)
except Exception as _autofill_import_error:  # pragma: no cover - env-dependent
    print(f"[tool_dispatch] Agent Autofill tools unavailable: {_autofill_import_error}")


def execute_tool(name: str, tool_input: dict, company_id: str):
    """
    Run one tool. Returns (result, is_error).

    Never raises for ordinary failures — the agent loop needs a tool_result
    block for every tool_use block, so failures come back as error results
    that Claude can read and recover from.
    """
    handler = TOOL_REGISTRY.get(name)
    if handler is None:
        return f"Unknown tool: {name}", True

    try:
        clean_input = _sanitize(tool_input, company_id)
    except ToolExecutionError as e:
        return str(e), True

    try:
        return handler(**clean_input), False
    except TypeError as e:
        # Model supplied the wrong argument names/count for this tool.
        return f"Invalid arguments for {name}: {e}", True
    except Exception as e:
        return f"{name} failed: {e}", True
