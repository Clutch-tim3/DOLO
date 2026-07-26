from agent.memory.company_store import get_company_profile, get_company_documents, update_company_profile, search_conversation_history, log_conversation
from agent.memory.tools_schema import memory_tools
from agent.quotation.extract_line_items import extract_line_items
from agent.quotation.price_search import get_prices_for_items
from agent.quotation.quote_builder import generate_quote_document, quotation_tools
from agent.quotation.quote_audit_log import log_draft_quote, finalize_quote
from agent.onboarding.vet_company import vet_company_document, onboarding_tools
from agent.navigation.app_help import get_app_help, app_help_tools
from agent.claude_client import call_claude_with_tracking
import json

SYSTEM_PROMPT = """
You are the Agent for the Donington Vale procurement platform (DOLO). 
Your capabilities are: company memory retrieval, app navigation help, quotation generation, onboarding vetting, and calendar assistance.

HARD CONSTRAINTS:
- Never fabricate a price. If uncertain, flag for manual review.
- Never state a company fact not retrieved via tool call this session.
- Never describe an app feature not present in the static knowledge base.
- Never present onboarding advice as compliance-verified or guaranteed. It is always non-binding guidance.
- Company memory lives in the DB, not your context. You must call get_company_profile and get_company_documents at the start of every session before answering anything company-specific.
- You must not write to the company profile without explicit per-field user confirmation.
"""

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
    
    doc = generate_quote_document(profile, priced_items, is_final=False)
    return {
        "quote_id": quote_id,
        "draft_document": doc["document"],
        "has_flags": doc["has_flags"]
    }

def finalize_quote_flow(quote_id: str, priced_items: list):
    """
    Attempts to finalize the quote.
    """
    doc = generate_quote_document({}, priced_items, is_final=True)
    if doc.get("status") == "error":
        return doc["message"]
        
    finalize_quote(quote_id, priced_items)
    return "Quote finalized successfully!"

def process_agent_chat(company_id: str, user_message: str):
    """
    Handles a generic chat message from the user, calling Claude with tools.
    """
    messages = [{"role": "user", "content": user_message}]
    
    # Combine all tools
    all_tools = memory_tools + quotation_tools + onboarding_tools + app_help_tools
    
    # Call Claude (Requires max_tokens to be set)
    response = call_claude_with_tracking(
        company_id=company_id,
        messages=messages,
        system=SYSTEM_PROMPT,
        tools=all_tools,
        max_tokens=800
    )
    
    # Process potential tool calls or just return text
    # (For this test, we return the text content, and stringify tool calls if any)
    reply_text = response.get("content", "")
    tool_calls = response.get("tool_calls", [])
    
    if tool_calls:
        reply_text += "\n\n[Agent requested tools:]\n" + json.dumps(tool_calls, indent=2)
        
    if not reply_text:
        reply_text = "*(No text response)*"
        
    return reply_text

