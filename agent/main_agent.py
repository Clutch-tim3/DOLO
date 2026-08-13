from agent.memory.company_store import get_company_profile, get_company_documents, update_company_profile, search_conversation_history, log_conversation
from agent.memory.tools_schema import memory_tools
from agent.quotation.extract_line_items import extract_line_items
from agent.quotation.price_search import get_prices_for_items
from agent.quotation.quote_builder import generate_quote_document, quotation_tools
from agent.quotation.quote_audit_log import log_draft_quote, finalize_quote
from agent.onboarding.vet_company import vet_company_document, onboarding_tools
from agent.navigation.app_help import get_app_help, app_help_tools
from agent.claude_client import call_claude_with_tracking
from agent.tool_dispatch import execute_tool
import json
import logging

logger = logging.getLogger(__name__)

# Ceiling on request/execute round-trips per user message. Without this a model
# that keeps re-calling a failing tool would loop until the rate limiter stops it.
MAX_TOOL_ITERATIONS = 8

# Needs headroom for thinking + tool_use blocks + the final answer. The old 800
# truncated multi-tool turns mid-block.
MAX_TOKENS_PER_TURN = 4096

SYSTEM_PROMPT = """
You are the CairoAI Agent, the assistant built into the CairoAI procurement platform.
Your capabilities are: company memory retrieval, app navigation help, quotation generation, onboarding vetting, and calendar assistance.

IDENTITY:
- The product is called CairoAI. That is the only name for it.
- Refer to yourself as the CairoAI Agent, and to the product as CairoAI.
- The platform was previously called DOLO. Never use that name, and do not
  mention it even if a user asks about it or a document refers to it — say
  CairoAI instead.

HARD CONSTRAINTS:
- Never fabricate a price. If uncertain, flag for manual review.
- Never state a company fact not retrieved via tool call this session.
- Never describe an app feature not present in the static knowledge base.
- Never present onboarding advice as compliance-verified or guaranteed. It is always non-binding guidance.
- Company memory lives in the DB, not your context. You must call get_company_profile and get_company_documents at the start of every session before answering anything company-specific.
- You must not write to the company profile without explicit per-field user confirmation.

SESSION CONTEXT:
- The company_id for this session is: {company_id}
- Use that value for the company_id argument of every tool that takes one. Never
  ask the user for their company_id and never guess one — you already have it.

ACCREDITATION VETTING FLOW:
- The Compliance Vault requires five documents: Tax Clearance, B-BBEE Certificate,
  CIDB Grading, CSD Report and CIPC Registration. Call get_vault_status to see
  which are present.
- While documents are still missing, do not vet. Say which ones are outstanding
  and that you will run the vet as soon as the vault is complete.
- Once all five are present, vet the company: read parsed_company_facts from
  get_vault_status to establish what this company actually does — the CSD report's
  industry and commodity categories are the reliable signal, not assumptions from
  the company name.
- Then call generate_accreditation_report with the accreditations, licences and
  certifications that genuinely fit that line of work and would improve their
  standing on South African government tenders. Give each a real https link to the
  issuing body, an honest priority, and a reason written for THIS company.
- Judge the list on accuracy, not length. A short correct list beats a padded one.
  Do not recommend something because it sounds impressive if it does not apply to
  their commodity categories.
- Hand the user the returned pdf_url. The PDF carries the non-binding disclaimer,
  so summarise the top items in chat rather than restating the whole document.

AGENT AUTOFILL FLOW:
- autofill_prepare_tender pre-fills a bid form AND returns the eligibility /
  win-probability verdict for the same document. Give the user both: whether they
  can bid at all comes first, then what was filled.
- What it produces is a DRAFT. Never call it complete, ready, final or
  submission-ready, and never say a signature, price or declaration was handled.
- Each flagged field must be acknowledged separately with
  autofill_acknowledge_field. Walk the user through them one at a time, quoting
  the field key and label, and pass on what they actually say about that field.
  Do not invent a note, do not reuse one note across fields, and do not offer to
  acknowledge them all at once — there is no call that does that.
- Only call autofill_export_document with as_reviewed=true once every flagged
  field is acknowledged. If it refuses, read out which fields are still open
  rather than retrying.

TONE:
- Speak the way a capable executive secretary speaks to the person they work
  for: warm, composed, and deferential without being servile. You are briefing
  someone whose time is short.
- Lead with the answer, then the detail. Never open with filler.
- Offer rather than instruct: "I'll draft that now unless you'd rather I
  waited" instead of "You should do X".
- Say plainly when something is not on file or not something you can do, and
  say what you will do instead. Never cover a gap with padding.
- Do not use pet names or honorifics, and do not guess at how to address the
  user.

OUTPUT FORMAT:
- The chat window renders your reply as plain text, so markdown syntax is shown
  literally. Do not use **bold**, *italics*, `backticks`, # headings or [](links).
- For lists, write each item on its own line starting with "- ". Use blank lines
  between paragraphs. Keep replies short and conversational.
"""


def build_system_prompt(company_id: str) -> str:
    """
    The tool schemas mark company_id as required, so the model needs its value.
    Without it Claude stops and asks the user for an ID they don't know, and the
    company-memory tools never fire. Injecting it here is safe because
    tool_dispatch pins company_id server-side regardless of what the model sends.
    """
    return SYSTEM_PROMPT.format(company_id=company_id)

def generate_draft_quote_flow(company_id: str, tender_file_path: str):
    """
    Executes the full quotation pipeline.
    """
    profile = get_company_profile(company_id)
    if not profile:
        return "Error: Company profile not found."
        
    items = extract_line_items(tender_file_path)
    priced_items = get_prices_for_items(items)
    
    quote_id = log_draft_quote(company_id, "TENDER_1", priced_items)
    
    doc = generate_quote_document(profile, priced_items, is_final=False,
                                  company_id=company_id)
    return {
        "quote_id": quote_id,
        "draft_document": doc["document"],
        "has_flags": doc["has_flags"],
        # generate_quote_document writes the PDF and returns its URL; this used to
        # be dropped here, so the download button had nothing to point at.
        "pdf_url": doc.get("pdf_url"),
    }

def finalize_quote_flow(quote_id: str, priced_items: list = None, company_id: str = None):
    """
    Attempts to finalize the quote.

    The flag check runs against what was recorded at draft time, never against
    `priced_items`. This function is reachable from TOOL_REGISTRY via
    finalize_quotation, so its arguments are model-supplied and must be treated
    as untrusted: passing a list with the flags scrubbed used to finalize a
    flagged quote outright, and overwrite the stored flags on the way through.
    `priced_items` is now advisory only — the document is built from stored
    state. To clear a flag, write the price into stored state through
    resolve_quote_item.
    """
    from agent.quotation.quote_audit_log import (
        flagged_items,
        get_quote_record,
    )

    # An omitted company_id used to skip the ownership check entirely, which
    # finalised another company's quote outright. It is not model-reachable —
    # execute_tool injects the session value — but a direct caller could hit
    # it, so absence is refused rather than treated as "no check needed".
    if not company_id:
        return "Quote not found."

    record = get_quote_record(quote_id)
    # Same message for "no such quote" and "not yours" — a distinct
    # "not yours" would confirm the id exists.
    if not record or record["company_id"] != company_id:
        return "Quote not found."
    if record["status"] == "FINAL":
        return "This quote is already final."

    stored_items = record["line_items"]
    outstanding = flagged_items(stored_items)
    if outstanding:
        listed = "\n".join(f"- {f['description']}" for f in outstanding)
        return ("Cannot finalize quote: unresolved flagged items.\n" + listed +
                "\nEach needs a price confirmed before this quote can be final.")

    doc = generate_quote_document({}, stored_items, is_final=True,
                                  company_id=record["company_id"])
    if doc.get("status") == "error":
        return doc["message"]

    if not finalize_quote(quote_id, stored_items, company_id=record["company_id"]):
        # Lost a race, or the row moved out of DRAFT between the read and the
        # write. Refusing is the safe outcome.
        return "Quote could not be finalized; please reload it and try again."
    return "Quote finalized successfully!"

def _autofill_tools() -> list:
    """
    Agent Autofill's tool schemas, or nothing.

    Imported at call time and behind a catch for the same reason tool_dispatch
    registers the handlers that way: a partially built agent_autofill package
    must not be able to stop the agent from answering a question about the
    company profile. A missing schema costs a feature; an ImportError at module
    scope costs every feature.
    """
    try:
        from agent_autofill.integration.autofill_tools import autofill_tools

        return list(autofill_tools)
    except Exception:
        logger.warning("Agent Autofill tools unavailable", exc_info=True)
        return []


def _tool_result_content(result) -> str:
    """Tool results must be text (or content blocks) — serialize anything else."""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, default=str)
    except (TypeError, ValueError):
        return str(result)


def process_agent_chat(company_id: str, user_message: str):
    """
    Handles a chat message from the user, running the full agentic tool loop:

        send  -> stop_reason == "tool_use"? -> execute tools locally
              -> append tool_result blocks  -> send again
              -> repeat until stop_reason == "end_turn"

    Returns a dict:
        {"message": <final assistant text>, "pdf_url": <str|None>, "tools_used": [...]}

    Previously this made a single call and, if Claude asked for a tool, pasted
    the raw JSON of the request into the chat bubble — the tools never ran.
    """
    messages = [{"role": "user", "content": user_message}]
    all_tools = memory_tools + quotation_tools + onboarding_tools + app_help_tools
    all_tools = all_tools + _autofill_tools()
    system_prompt = build_system_prompt(company_id)

    tools_used = []
    pdf_url = None
    download_name = None
    reply_text = ""

    for _ in range(MAX_TOOL_ITERATIONS):
        response = call_claude_with_tracking(
            company_id=company_id,
            messages=messages,
            system=system_prompt,
            tools=all_tools,
            max_tokens=MAX_TOKENS_PER_TURN,
        )

        reply_text = response.get("content", "") or reply_text

        if response.get("stop_reason") != "tool_use":
            break

        # Preserve the assistant turn verbatim — the tool_use blocks in it are
        # what the tool_result blocks below refer to by id.
        messages.append({"role": "assistant", "content": response["blocks"]})

        tool_results = []
        for call in response.get("tool_calls", []):
            result, is_error = execute_tool(call["name"], call["input"], company_id)
            tools_used.append(call["name"])

            if not is_error and isinstance(result, dict) and result.get("pdf_url"):
                pdf_url = result["pdf_url"]
                # Autofill exports are .docx, so the client cannot assume the
                # generated file is a quotation PDF and name it accordingly.
                download_name = result.get("download_name") or download_name

            if is_error:
                logger.warning(
                    "Agent tool failed",
                    extra={"company_id": company_id, "extra_data": {"tool": call["name"], "error": str(result)}},
                )

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": call["id"],
                "content": _tool_result_content(result),
                "is_error": is_error,
            })

        # All results for one assistant turn go back in a single user message.
        messages.append({"role": "user", "content": tool_results})
    else:
        # Loop ran to the cap without Claude finishing its turn.
        logger.warning(
            "Agent hit MAX_TOOL_ITERATIONS",
            extra={"company_id": company_id, "extra_data": {"tools_used": tools_used}},
        )
        reply_text = reply_text or (
            "I wasn't able to finish that — I kept needing more steps than I'm "
            "allowed in one go. Try narrowing the request."
        )

    if not reply_text:
        reply_text = "*(No text response)*"

    try:
        log_conversation(company_id, user_message, reply_text, tools_used)
    except Exception:
        logger.exception("Failed to log conversation")

    return {
        "message": reply_text,
        "pdf_url": pdf_url,
        "download_name": download_name,
        "tools_used": tools_used,
    }

