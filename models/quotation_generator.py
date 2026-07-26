"""
quotation_generator.py
======================
Generates custom, highly professional South African procurement PDF quotations.
Includes company details, itemized pricing table, SA preferential point calculations (80/20 or 90/10),
and LLM-generated executive summaries via x.ai/Grok / Gemini / Groq fallback.
"""

import os
import sys
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

# Add DOLO root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.sa_scoring import calculate_total_sa_score

# ReportLab imports
try:
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


def call_llm_for_proposal_summary(supplier_name: str, tender_title: str, total_price_zar: float) -> str:
    """
    Calls LLM API (x.ai / Grok -> Gemini -> Groq -> Local Fallback) to generate
    a 2-paragraph executive proposal introduction for the quotation.
    """
    prompt = (
        f"Write a professional, 2-paragraph executive quotation proposal cover summary for South African government procurement.\n"
        f"Supplier Name: {supplier_name}\n"
        f"Tender Title/Subject: {tender_title}\n"
        f"Total Bid Value: R {total_price_zar:,.2f} (Incl. VAT)\n"
        f"Keep the tone formal, highly competent, emphasizing quality, compliance with PPPFA regulations, and timely delivery."
    )

    # 1. Try x.ai / Grok
    xai_key = os.getenv("XAI_API_KEY")
    if xai_key:
        try:
            import urllib.request
            headers = {"Authorization": f"Bearer {xai_key}", "Content-Type": "application/json"}
            payload = {
                "messages": [
                    {"role": "system", "content": "You are a professional South African procurement proposal writer."},
                    {"role": "user", "content": prompt}
                ],
                "model": "grok-beta",
                "temperature": 0.3
            }
            req = urllib.request.Request("https://api.x.ai/v1/chat/completions", data=json.dumps(payload).encode('utf-8'), headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"Quotation LLM (x.ai) fallback: {e}")

    # 2. Try Gemini
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            if resp and resp.text:
                return resp.text.strip()
        except Exception as e:
            print(f"Quotation LLM (Gemini) fallback: {e}")

    # 3. Default fallback
    return (
        f"{supplier_name} is pleased to submit this formal quotation for '{tender_title}'. "
        f"Our team possesses full technical capacity, active compliance registrations (CSD, B-BBEE, Tax Pin), and a proven track record "
        f"in delivering high-grade public sector services within stipulated deadlines.\n\n"
        f"The total evaluated bid price of R {total_price_zar:,.2f} (Inclusive of 15% VAT) reflects competitive, value-engineered pricing "
        f"strictly structured under standard PPPFA preferential procurement framework guidelines."
    )


def generate_quotation_pdf(
    supplier_info: dict,
    tender_title: str,
    line_items: list,
    output_path: Path,
    lowest_competing_price: float = None,
    evaluation_system: str = "80/20"
) -> Path:
    """
    Generates a high-quality PDF quotation document.
    """
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError("ReportLab library is not installed.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    supplier_name = supplier_info.get("company_name", "BIDDING SUPPLIER").upper()
    reg_no        = supplier_info.get("registration_number", "N/A")
    csd_no        = supplier_info.get("csd_number", supplier_info.get("csd_supplier_number", "N/A"))
    bbbee_lvl     = supplier_info.get("bbbee_level", 1)
    cidb_grade    = supplier_info.get("cidb_grade", "N/A")

    # Pricing calculations
    subtotal = sum(float(item.get("qty", 1)) * float(item.get("unit_price", 0)) for item in line_items)
    vat_amount = subtotal * 0.15
    total_zar = subtotal + vat_amount

    # SA Scoring
    lowest_price = lowest_competing_price if lowest_competing_price and lowest_competing_price > 0 else (subtotal * 0.90)
    sa_res = calculate_total_sa_score(
        supplier_price=subtotal,
        lowest_competing_price=lowest_price,
        bbbee_level=bbbee_lvl,
        tender_value_zar=total_zar,
        evaluation_system_override=evaluation_system
    )

    # Executive Summary text
    exec_summary = call_llm_for_proposal_summary(supplier_name, tender_title, total_zar)

    # Build PDF with ReportLab
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=20,
        textColor=colors.HexColor('#C5A880'),
        spaceAfter=6
    )

    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        textColor=colors.HexColor('#666666'),
        spaceAfter=12
    )

    body_style = ParagraphStyle(
        'DocBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#222222'),
        spaceAfter=10
    )

    header_label_style = ParagraphStyle(
        'HeaderLabel',
        fontName='Helvetica-Bold',
        fontSize=8,
        textColor=colors.HexColor('#C5A880')
    )

    header_val_style = ParagraphStyle(
        'HeaderVal',
        fontName='Helvetica',
        fontSize=8,
        textColor=colors.HexColor('#111111')
    )

    elements = []

    # Header block
    elements.append(Paragraph("FORMAL TENDER QUOTATION", title_style))
    elements.append(Paragraph(f"Tender Reference: {tender_title} | Date: {datetime.now().strftime('%d %B %Y')}", subtitle_style))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#C5A880'), spaceAfter=15))

    # Supplier Info Table (2-column layout)
    info_data = [
        [
            Paragraph("SUPPLIER DETAILS", header_label_style),
            Paragraph("COMPLIANCE REGISTRATIONS", header_label_style)
        ],
        [
            Paragraph(f"<b>Company:</b> {supplier_name}<br/><b>Reg No:</b> {reg_no}<br/><b>Status:</b> Active Registered Vendor", header_val_style),
            Paragraph(f"<b>CSD Number:</b> {csd_no}<br/><b>B-BBEE Level:</b> Level {bbbee_lvl}<br/><b>CIDB Designation:</b> {cidb_grade}", header_val_style)
        ]
    ]

    info_table = Table(info_data, colWidths=[260, 260])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8F9FA')),
        ('PADDING', (0,0), (-1,-1), 8),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#E0E0E0')),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 15))

    # Executive Proposal Cover
    elements.append(Paragraph("EXECUTIVE PROPOSAL SUMMARY", ParagraphStyle('SubHead', fontName='Helvetica-Bold', fontSize=11, textColor=colors.HexColor('#111111'), spaceAfter=6)))
    for p in exec_summary.split("\n\n"):
        if p.strip():
            elements.append(Paragraph(p.strip(), body_style))
    elements.append(Spacer(1, 10))

    # Line Items Table
    elements.append(Paragraph("ITEMIZED PRICING SCHEDULE", ParagraphStyle('SubHead', fontName='Helvetica-Bold', fontSize=11, textColor=colors.HexColor('#111111'), spaceAfter=6)))

    table_data = [["Item #", "Description", "Qty", "Unit Price (ZAR)", "Total Price (ZAR)"]]
    for idx, item in enumerate(line_items, 1):
        qty = float(item.get("qty", 1))
        unit_p = float(item.get("unit_price", 0))
        tot_p = qty * unit_p
        table_data.append([
            str(idx),
            str(item.get("description", "Service Item")),
            f"{qty:g}",
            f"R {unit_p:,.2f}",
            f"R {tot_p:,.2f}"
        ])

    # Add Subtotal, VAT, Total
    table_data.append(["", "", "", "Subtotal (Excl. VAT):", f"R {subtotal:,.2f}"])
    table_data.append(["", "", "", "15% VAT:", f"R {vat_amount:,.2f}"])
    table_data.append(["", "", "", "TOTAL EVALUATED BID:", f"R {total_zar:,.2f}"])

    item_table = Table(table_data, colWidths=[40, 240, 40, 100, 100])
    item_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1A1A1A')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#C5A880')),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 8),
        ('ALIGN', (2,0), (-1,-1), 'RIGHT'),
        ('GRID', (0,0), (-1,-4), 0.5, colors.HexColor('#E0E0E0')),
        ('FONTNAME', (3,-3), (-1,-1), 'Helvetica-Bold'),
        ('BACKGROUND', (3,-1), (-1,-1), colors.HexColor('#F4EFEA')),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    elements.append(item_table)
    elements.append(Spacer(1, 15))

    # PPPFA Preferential Score Summary
    elements.append(Paragraph("PREFERENTIAL PROCUREMENT SCORECARD (PPPFA)", ParagraphStyle('SubHead', fontName='Helvetica-Bold', fontSize=11, textColor=colors.HexColor('#111111'), spaceAfter=6)))

    score_data = [
        ["Framework", "Price Points", "B-BBEE Points", "Total Preference Score", "Competitive Rating"],
        [
            sa_res["evaluation_system"],
            f"{sa_res['price_score']:.1f} / {'80' if sa_res['evaluation_system'] == '80/20' else '90'}",
            f"{sa_res['bbbee_points']:.1f} / {'20' if sa_res['evaluation_system'] == '80/20' else '10'}",
            f"{sa_res['total_score']:.1f} / 100",
            sa_res["competitive_position"]
        ]
    ]
    score_table = Table(score_data, colWidths=[100, 100, 100, 110, 110])
    score_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F0F0F0')),
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CCCCCC')),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    elements.append(score_table)

    # Footer notice
    elements.append(Spacer(1, 20))
    elements.append(Paragraph("<i>This quotation is generated electronically under official compliance protocols. Valid for 90 days from date of issuance.</i>", ParagraphStyle('Foot', fontName='Helvetica-Oblique', fontSize=7, textColor=colors.HexColor('#888888'), alignment=1)))

    doc.build(elements)
    return output_path
