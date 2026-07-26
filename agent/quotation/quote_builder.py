from agent.quotation.quote_audit_log import finalize_quote

def generate_quote_document(company_profile: dict, priced_items: list, is_final: bool = False) -> dict:
    """
    Assembles the quote document.
    Enforces the 'Cannot finalize' rule if flags exist.
    """
    
    has_flags = any(item.get("price_status") in ["MANUAL_REVIEW_REQUIRED", "LOW_CONFIDENCE"] for item in priced_items)
    
    if is_final and has_flags:
        return {
            "status": "error",
            "message": "Cannot finalize quote: there are unresolved flagged items (MANUAL_REVIEW_REQUIRED or LOW_CONFIDENCE). Please confirm these prices manually."
        }
        
    doc_lines = []
    doc_lines.append(f"QUOTATION")
    doc_lines.append(f"Company: {company_profile.get('company_name', 'Unknown')}")
    doc_lines.append(f"Reg No: {company_profile.get('registration_number', 'N/A')}")
    doc_lines.append(f"B-BBEE Level: {company_profile.get('bbbee_level', 'Unknown')}")
    doc_lines.append("-" * 40)
    
    total_quote = 0.0
    for item in priced_items:
        desc = item["description"]
        qty = item["quantity"]
        unit = item["unit"]
        price = item.get("price")
        total = item.get("total")
        status = item.get("price_status")
        source = item.get("retailer_name", "Unknown Source")
        date_checked = item.get("timestamp", "")[:10]
        
        flag_str = ""
        if status in ["MANUAL_REVIEW_REQUIRED", "LOW_CONFIDENCE"]:
            flag_str = f" [FLAG: {status}]"
            
        if price is None:
            doc_lines.append(f"- {qty} {unit} x {desc}: TBD{flag_str}")
        else:
            doc_lines.append(f"- {qty} {unit} x {desc}: R{price:.2f} (Total: R{total:.2f}){flag_str}")
            doc_lines.append(f"  Source: {source} (Checked: {date_checked})")
            total_quote += total
            
    doc_lines.append("-" * 40)
    doc_lines.append(f"Total: R{total_quote:.2f}")
    
    disclaimer = (
        "\n[DISCLAIMER] Pricing sourced from public retailer data. Prices are subject to "
        "change and should be independently verified before submission. This document is a draft "
        "prepared with AI assistance and requires review before use."
    )
    doc_lines.append(disclaimer)
    
    return {
        "status": "success",
        "document": "\n".join(doc_lines),
        "has_flags": has_flags
    }

quotation_tools = [
    {
        "name": "generate_draft_quote",
        "description": "Extracts line items from a tender document, searches for prices, logs the audit trail, and generates a draft quotation document.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string"
                },
                "tender_file_path": {
                    "type": "string"
                }
            },
            "required": ["company_id", "tender_file_path"]
        }
    },
    {
        "name": "finalize_quotation",
        "description": "Attempts to mark a quotation as final. WILL FAIL if any line items are still flagged as MANUAL_REVIEW_REQUIRED or LOW_CONFIDENCE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "quote_id": {
                    "type": "string"
                },
                "confirmed_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "description": "List of the human-verified item prices"
                    }
                }
            },
            "required": ["quote_id", "confirmed_items"]
        }
    }
]
