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


TOOL_REGISTRY = {
    "get_company_profile": lambda company_id: get_company_profile(company_id),
    "get_company_documents": lambda company_id: get_company_documents(company_id),
    "update_company_profile": lambda company_id, fields: update_company_profile(company_id, fields),
    "search_conversation_history": lambda company_id, query=None: search_conversation_history(
        company_id, query
    ),
    "get_app_help": lambda feature_query: get_app_help(feature_query),
    "vet_company_document": lambda company_id, file_path, doc_type="UNKNOWN": vet_company_document(
        company_id, file_path, doc_type
    ),
    "generate_draft_quote": _generate_draft_quote,
    "finalize_quotation": _finalize_quotation,
}


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
