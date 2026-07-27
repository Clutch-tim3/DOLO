import os
import uuid
from fpdf import FPDF
from agent.quotation.quote_audit_log import finalize_quote

def generate_quote_document(company_profile: dict, priced_items: list, is_final: bool = False) -> dict:
    """
    Assembles the quote document as a PDF with Clive Red styling.
    Enforces the 'Cannot finalize' rule if flags exist.
    """
    
    has_flags = any(item.get("price_status") in ["MANUAL_REVIEW_REQUIRED", "LOW_CONFIDENCE"] for item in priced_items)
    
    if is_final and has_flags:
        return {
            "status": "error",
            "message": "Cannot finalize quote: there are unresolved flagged items. Please confirm these prices manually."
        }
        
    company_name = company_profile.get('company_name', 'Donington Vale')
    reg_no = company_profile.get('registration_number', '2026/250499/07')
    bbbee = company_profile.get('bbbee_level', 'Unknown')
    
    pdf = FPDF()
    pdf.add_page()
    
    # Signature Light Mode Colors: Clive Red (#C8331F) = (200, 51, 31)
    pdf.set_fill_color(200, 51, 31)
    pdf.rect(0, 0, 210, 30, 'F')
    
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", 'B', 24)
    pdf.text(15, 20, company_name.upper())
    
    pdf.set_font("Arial", '', 10)
    pdf.text(150, 16, f"Reg: {reg_no}")
    pdf.text(150, 22, f"B-BBEE: {bbbee}")
    
    pdf.set_text_color(200, 51, 31)
    pdf.set_font("Arial", 'B', 16)
    pdf.text(105, 50, "QUOTATION" if is_final else "DRAFT QUOTATION")
    
    pdf.set_draw_color(200, 51, 31)
    pdf.set_line_width(0.5)
    pdf.line(15, 55, 195, 55)
    
    pdf.set_text_color(40, 40, 40)
    pdf.set_font("Arial", '', 11)
    
    pdf.text(15, 65, "Item Description")
    pdf.text(130, 65, "Qty")
    pdf.text(150, 65, "Unit Price")
    pdf.text(180, 65, "Total")
    
    pdf.line(15, 68, 195, 68)
    
    y = 78
    total_quote = 0.0
    for item in priced_items:
        desc = item["description"]
        qty = item["quantity"]
        price = item.get("price")
        total = item.get("total", 0)
        
        pdf.text(15, y, str(desc)[:50])
        pdf.text(130, y, str(qty))
        
        if price is None:
            pdf.text(150, y, "TBD")
            pdf.text(180, y, "TBD")
        else:
            pdf.text(150, y, f"R {price:,.2f}")
            pdf.text(180, y, f"R {total:,.2f}")
            total_quote += total
            
        y += 10
        if y > 250:
            pdf.add_page()
            y = 20
            
    pdf.line(15, y, 195, y)
    y += 10
    
    pdf.set_font("Arial", 'B', 11)
    pdf.text(150, y, "Subtotal:")
    pdf.text(180, y, f"R {total_quote:,.2f}")
    
    y += 10
    vat = total_quote * 0.15
    pdf.text(150, y, "VAT (15%):")
    pdf.text(180, y, f"R {vat:,.2f}")
    
    y += 10
    pdf.set_text_color(200, 51, 31)
    pdf.text(150, y, "TOTAL DUE:")
    pdf.text(180, y, f"R {(total_quote + vat):,.2f}")
    
    pdf.set_font("Arial", '', 9)
    pdf.set_text_color(150, 150, 150)
    
    disclaimer = "This is an AI-generated draft quotation. Prices are subject to change and must be independently verified."
    if is_final:
        disclaimer = "This is a finalized quotation. Prices are valid for 30 days."
        
    pdf.text(20, 275, disclaimer)
    
    filename = f"quote_{uuid.uuid4().hex[:8]}.pdf"
    os.makedirs("static/downloads", exist_ok=True)
    filepath = os.path.join("static", "downloads", filename)
    pdf.output(filepath)
    
    return {
        "status": "success",
        "document": "PDF Quote successfully generated.",
        "pdf_url": f"/static/downloads/{filename}",
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
