import os
import uuid
from fpdf import FPDF
from agent.file_paths import generated_dir, public_url
from agent.quotation.quote_audit_log import finalize_quote

def generate_quote_document(company_profile: dict, priced_items: list, is_final: bool = False) -> dict:
    has_flags = any(item.get("price_status") in ["MANUAL_REVIEW_REQUIRED", "LOW_CONFIDENCE"] for item in priced_items)
    if is_final and has_flags:
        return {"status": "error", "message": "Cannot finalize quote: unresolved flagged items."}
        
    company_name = company_profile.get('company_name', 'CAIROAI')
    reg_no = company_profile.get('registration_number', '2026/250499/07')
    bbbee = company_profile.get('bbbee_level', 'Level 2 Contributor')
    
    pdf = FPDF()
    # PAGE 1: Cover Page
    pdf.add_page()
    pdf.set_fill_color(200, 51, 31)
    pdf.rect(0, 0, 210, 40, 'F')
    
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", 'B', 20)
    pdf.cell(0, 20, "NATIONAL HEALTH LABORATORY SERVICE", ln=1, align='C')
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "INVITATION FOR BID - RESPONSE DOCUMENT", ln=1, align='C')
    
    pdf.set_text_color(40, 40, 40)
    pdf.set_font("Arial", '', 12)
    pdf.ln(15)
    pdf.cell(0, 10, "BID NUMBER: RFB029/26/27", ln=1, align='C')
    pdf.set_font("Arial", '', 10)
    pdf.cell(0, 5, "DESCRIPTION: Outright Purchase of an Automated Ampoule Filling and Sealing Machine", ln=1, align='C')
    pdf.cell(0, 5, "including Service and Maintenance for a Period of Five (5) Years for SAVP", ln=1, align='C')
    
    pdf.ln(10)
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(0, 10, "CLOSING DATE: 21 August 2026 | VALIDITY PERIOD: 180 Days", ln=1, align='C')
    
    pdf.ln(15)
    pdf.set_fill_color(245, 230, 230)
    pdf.set_text_color(200, 51, 31)
    pdf.cell(0, 10, "CONFIDENTIAL - PROPRIETARY DOCUMENT", border=0, ln=1, align='C', fill=True)
    
    pdf.ln(10)
    pdf.set_text_color(255, 255, 255)
    pdf.set_fill_color(200, 51, 31)
    pdf.cell(0, 8, " PART A: SUPPLIER INFORMATION", border=1, ln=1, align='L', fill=True)
    
    pdf.set_text_color(40, 40, 40)
    pdf.set_font("Arial", '', 9)
    def add_row(k, v):
        pdf.set_font("Arial", 'B', 9)
        pdf.cell(60, 8, f" {k}", border=1)
        pdf.set_font("Arial", '', 9)
        pdf.cell(130, 8, f" {v}", border=1, ln=1)
        
    add_row("NAME OF BIDDER", company_name)
    add_row("VAT REGISTRATION NUMBER", "4120256789")
    add_row("CSD No / TCS PIN", "CSD: 1234567890 / TCS PIN: TCS-2026")
    add_row("B-BBEE STATUS LEVEL", bbbee)
    add_row("COMPANY REGISTRATION", reg_no)
    
    # PAGE 2: Specs & Pricing
    pdf.add_page()
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 8, " ANNEXURE B: PRICING SCHEDULE", border=1, ln=1, align='L', fill=True)
    
    pdf.set_text_color(40, 40, 40)
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(90, 8, " Description", border=1)
    pdf.cell(20, 8, " Qty", border=1)
    pdf.cell(40, 8, " Unit Price", border=1)
    pdf.cell(40, 8, " Total", border=1, ln=1)
    
    pdf.set_font("Arial", '', 9)
    total_quote = 0.0
    unpriced = 0
    for item in priced_items:
        desc = str(item["description"])[:45]
        qty = str(item["quantity"])
        price = item.get("price")
        total = item.get("total")

        # price_search deliberately returns price/total = None for anything it
        # can't source confidently (price_status MANUAL_REVIEW_REQUIRED). That
        # is the normal flagged path, not an error — it used to raise
        # `TypeError: unsupported operand +=: float and NoneType` here and kill
        # the whole quote. Such rows are shown as TBC and left out of the
        # subtotal so the draft never implies a price nobody quoted.
        if total is None or price is None:
            unpriced += 1
            pdf.cell(90, 8, f" {desc}", border=1)
            pdf.cell(20, 8, f" {qty}", border=1)
            pdf.cell(40, 8, " TBC - MANUAL REVIEW", border=1)
            pdf.cell(40, 8, " TBC", border=1, ln=1)
            continue

        total_quote += total
        pdf.cell(90, 8, f" {desc}", border=1)
        pdf.cell(20, 8, f" {qty}", border=1)
        pdf.cell(40, 8, f" R {price:,.2f}", border=1)
        pdf.cell(40, 8, f" R {total:,.2f}", border=1, ln=1)

    pdf.set_font("Arial", 'B', 9)
    pdf.cell(150, 8, " SUBTOTAL (VAT EXCL.):", border=1, align='R')
    pdf.cell(40, 8, f" R {total_quote:,.2f}", border=1, ln=1)
    vat = total_quote * 0.15
    pdf.cell(150, 8, " VAT (15%):", border=1, align='R')
    pdf.cell(40, 8, f" R {vat:,.2f}", border=1, ln=1)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(150, 8, " TOTAL PRICE (VAT INCL.):", border=1, align='R', fill=True)
    pdf.cell(40, 8, f" R {(total_quote + vat):,.2f}", border=1, ln=1, fill=True)
    
    pdf.ln(6)
    if unpriced:
        # Without this the totals above read as a complete, quotable price
        # when some line items were never actually priced.
        pdf.set_font("Arial", 'B', 9)
        pdf.set_text_color(200, 51, 31)
        pdf.multi_cell(
            0,
            5,
            f"INCOMPLETE DRAFT: {unpriced} line item(s) could not be priced and are "
            f"marked TBC above. The totals shown EXCLUDE them and are not a final "
            f"quotation. Confirm those prices before issuing this document.",
            align='C',
        )
        pdf.ln(4)

    disclaimer = "This is an AI-generated draft Bid Response Document for RFB029/26/27."
    pdf.set_font("Arial", 'I', 8)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(0, 5, disclaimer, ln=1, align='C')
    
    # Written through file_paths rather than to a repo-relative directory: the
    # deployed bundle is read-only, so os.makedirs("static/downloads") raised
    # OSError in production and the quote failed at the point of delivery. The
    # old "/static/downloads/<name>" URL was wrong too - it only resolved
    # locally, via the StaticFiles mount, while the endpoint that serves
    # quotations reads from a different directory entirely.
    filename = f"quote_{uuid.uuid4().hex[:8]}.pdf"
    filepath = generated_dir() / filename
    pdf.output(str(filepath))

    return {
        "status": "success",
        "document": "Multi-page Bid Response Document successfully generated.",
        "pdf_url": public_url(filename),
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
