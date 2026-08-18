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


def call_llm_for_proposal_summary(supplier_name: str, tender_title: str,
                                  total_price_zar: float = None) -> str:
    """
    Calls LLM API (x.ai / Grok -> Gemini -> Groq -> Local Fallback) to generate
    a 2-paragraph executive proposal introduction for the quotation.

    `total_price_zar` is None when any line is unpriced. The summary must then
    state no total at all: with a zero it would assert "a total evaluated bid
    price of R 0.00", which on a bid document reads as an offer to supply for
    nothing rather than as a blank.
    """
    price_line = (
        f"Total Bid Value: R {total_price_zar:,.2f} (Incl. VAT)\n"
        if total_price_zar is not None else
        "Total Bid Value: NOT YET PRICED. Do not state, imply or estimate any "
        "total, amount, price or figure anywhere in the summary. Do not describe "
        "the pricing as competitive or value-engineered — there is no price yet.\n"
    )
    prompt = (
        f"Write a professional, 2-paragraph executive quotation proposal cover summary for South African government procurement.\n"
        f"Supplier Name: {supplier_name}\n"
        f"Tender Title/Subject: {tender_title}\n"
        f"{price_line}"
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

    # 3. Default fallback. This is the path that actually runs without an LLM
    #    key, so it is the one that reaches most documents — and it stated the
    #    total in prose, which is how "R 0.00" appeared in a rendered PDF.
    opening = (
        f"{supplier_name} is pleased to submit this formal quotation for '{tender_title}'. "
        f"Our team possesses full technical capacity, active compliance registrations (CSD, B-BBEE, Tax Pin), and a proven track record "
        f"in delivering high-grade public sector services within stipulated deadlines.\n\n"
    )
    if total_price_zar is None:
        return opening + (
            "Pricing for this quotation has not yet been completed. The lines marked "
            "TBC in the schedule below must be priced before this document is "
            "submitted, and the totals shown exclude them."
        )
    return opening + (
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

    # Pricing calculations.
    #
    # A line with no unit price is EXCLUDED rather than counted as zero, which
    # is what quote_document does when it renders it as TBC. `float(None)`
    # would also raise here, and counting it as 0 would be worse than raising:
    # it produces a subtotal that looks complete and is not.
    priced_items = [i for i in line_items if i.get("unit_price") not in (None, "")]
    any_unpriced = len(priced_items) != len(line_items)

    subtotal = sum(float(i.get("qty", 1)) * float(i["unit_price"]) for i in priced_items)
    vat_amount = subtotal * 0.15
    total_zar = subtotal + vat_amount

    # SA Scoring.
    #
    # `subtotal * 0.90` used to stand in for a missing competitor price, which
    # made every supplier exactly 11% above a rival who did not exist. Bids are
    # sealed; the price of one is not knowable from your own. Passing None means
    # calculate_total_sa_score withholds the price score and says why, rather
    # than scoring against an invention. See models/sa_scoring.py.
    #
    # A quotation with unpriced lines has no meaningful supplier price either,
    # so the score is withheld for that too.
    sa_res = calculate_total_sa_score(
        supplier_price=None if any_unpriced else subtotal,
        lowest_competing_price=lowest_competing_price
                               if (lowest_competing_price and lowest_competing_price > 0)
                               else None,
        bbbee_level=bbbee_lvl,
        tender_value_zar=total_zar if not any_unpriced else None,
        evaluation_system_override=evaluation_system
    )

    # Executive Summary text
    # The summary is written by a model and states the total in prose. With an
    # unpriced line it would assert "a total evaluated bid price of R 0.00" —
    # the same false commitment as the totals row, in a sentence. Pass None and
    # the summary omits the figure entirely.
    exec_summary = call_llm_for_proposal_summary(
        supplier_name, tender_title, None if any_unpriced else total_zar)

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
        unit = item.get("unit_price")
        # TBC, not R 0.00. A zero here is a price — it says this line is free,
        # which on a bid document is a commitment rather than a gap. The same
        # rule quote_document.py already follows.
        if unit in (None, ""):
            unit_cell, total_cell = "TBC", "TBC"
        else:
            unit_p = float(unit)
            unit_cell, total_cell = f"R {unit_p:,.2f}", f"R {qty * unit_p:,.2f}"
        table_data.append([
            str(idx),
            str(item.get("description", "Service Item")),
            f"{qty:g}",
            unit_cell,
            total_cell,
        ])

    # Add Subtotal, VAT, Total.
    #
    # When a line has no price these read TBC, not R 0.00. On a bid document a
    # total of R 0.00 is not a blank — it is an offer to supply for nothing,
    # and it would be read as one. This is the trap that makes withholding a
    # price harder than it looks: removing the invented figure without fixing
    # the totals produces a WORSE artefact than the invention did.
    if any_unpriced:
        sub_cell = vat_cell = total_cell_sum = "TBC"
    else:
        sub_cell = f"R {subtotal:,.2f}"
        vat_cell = f"R {vat_amount:,.2f}"
        total_cell_sum = f"R {total_zar:,.2f}"

    table_data.append(["", "", "", "Subtotal (Excl. VAT):", sub_cell])
    table_data.append(["", "", "", "15% VAT:", vat_cell])
    table_data.append(["", "", "", "TOTAL EVALUATED BID:", total_cell_sum])

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

    # Any of these can be withheld: the price score without a real competing
    # price, the B-BBEE points and the framework without a tender value. They
    # are printed as "—" rather than a number, because this table appears on a
    # document going to an organ of state and a 0.0 there reads as a measured
    # zero rather than an unknown.
    def _score(value, out_of):
        return "—" if value is None else f"{value:.1f} / {out_of}"

    eval_sys = sa_res["evaluation_system"]
    score_data = [
        ["Framework", "Price Points", "B-BBEE Points", "Total Preference Score", "Competitive Rating"],
        [
            eval_sys or "—",
            _score(sa_res["price_score"], "80" if eval_sys == "80/20" else "90"),
            _score(sa_res["bbbee_points"], "20" if eval_sys == "80/20" else "10"),
            _score(sa_res["total_score"], "100"),
            sa_res["competitive_position"] or "—",
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
    if any_unpriced:
        # The same notice quote_document.py prints. A reader must not have to
        # notice the TBCs themselves to know the document is not a final offer.
        elements.append(Paragraph(
            "<b>This quotation is incomplete.</b> Lines marked TBC have no price, "
            "are excluded from the total, and must be priced by a person before "
            "this is submitted.",
            ParagraphStyle('Incomplete', fontName='Helvetica-Bold', fontSize=8,
                           textColor=colors.HexColor('#8A1C1C'), spaceAfter=8)))

    elements.append(Paragraph("<i>This quotation is generated electronically under official compliance protocols. Valid for 90 days from date of issuance.</i>", ParagraphStyle('Foot', fontName='Helvetica-Oblique', fontSize=7, textColor=colors.HexColor('#888888'), alignment=1)))

    doc.build(elements)
    return output_path
