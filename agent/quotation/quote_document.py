"""
Render a quotation as the document a supplier would be proud to send.

WHY THIS EXISTS
---------------
The previous generator produced a functional page. A quotation is the first
thing a procurement officer sees from a supplier they may never have heard of,
and on a desk of thirty responses the one that looks considered gets read
differently from the one that looks generated.

The layout follows a real quotation the owner sends today: a wordmark and
tagline over the supplier's own contact block, a coloured spine down the right
edge, a bordered panel holding the letter, a line-item table with a dark header
and a tinted total, and a footer band carrying the directors and registration
number. Every colour comes from `quote_theme.palette()`, so this is one layout
wearing whatever a company's brand colour is.

VAT IS NOT ASSUMED
------------------
The generator this replaces added `subtotal * 0.15` unconditionally. A supplier
who is not VAT-registered cannot levy VAT, so that produced a quotation
charging tax the supplier is not entitled to collect, addressed to an organ of
state. VAT is charged here only when the profile actually carries a VAT number,
and the wording under the table changes to match — the reference document says
so in as many words:

    "... is not currently a VAT-registered vendor; the prices quoted above are
    therefore all-inclusive of costs and no VAT is levied."
"""

from __future__ import annotations

import logging
from pathlib import Path

from agent.quotation.quote_theme import palette

log = logging.getLogger("agent.quotation.document")

PAGE_W, PAGE_H = 595.28, 841.89          # A4 in points
MARGIN = 42.0
SPINE_W = 26.0                            # the coloured bar down the right edge
FOOTER_H = 34.0
PANEL_TOP_GAP = 18.0


def _hex(colour: str):
    from reportlab.lib.colors import HexColor

    return HexColor(colour)


def _money(value) -> str:
    """South African convention: R1 402,81 — space thousands, comma decimal."""
    try:
        text = f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"
    return "R" + text.replace(",", " ").replace(".", ",")


def render_quotation(
    output_path,
    *,
    company: dict,
    client: dict,
    reference: str,
    subject: str,
    line_items: list[dict],
    date_text: str,
    validity_text: str = "",
    location_text: str = "",
    closing_text: str = "",
) -> Path:
    """
    Draw the quotation. Returns the path written.

    `company` is a company_profile row. `line_items` are dicts with
    description, serial, qty, unit_price. Nothing here computes a price it was
    not given — a line with no unit price renders as TBC and is excluded from
    the total, because a quotation with an invented figure on it is the worst
    thing this system could produce.
    """
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as canvas_mod
    from reportlab.platypus import Paragraph, Table, TableStyle

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pal = palette(company.get("brand_colour"))
    band, accent, rule, tint = (_hex(pal["band"]), _hex(pal["accent"]),
                                _hex(pal["rule"]), _hex(pal["tint"]))
    on_band = _hex(pal["on_band"])

    name = (company.get("company_name") or "SUPPLIER").upper()
    tagline = (company.get("tagline") or company.get("industry") or "").upper()
    vat_no = (company.get("vat_registration_number") or "").strip()
    charges_vat = bool(vat_no)

    c = canvas_mod.Canvas(str(output_path), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"Quotation {reference}".strip())

    # ---------------------------------------------------------------- spine
    # A full-height bar on the right with a notch cut out of its top corner.
    # It is what makes the page recognisable at a glance in a stack.
    c.setFillColor(band)
    c.rect(PAGE_W - SPINE_W, 0, SPINE_W, PAGE_H, stroke=0, fill=1)
    c.setFillColor(_hex("#ffffff"))
    path = c.beginPath()
    path.moveTo(PAGE_W - SPINE_W, PAGE_H)
    path.lineTo(PAGE_W, PAGE_H)
    path.lineTo(PAGE_W, PAGE_H - 30)
    path.close()
    c.drawPath(path, stroke=0, fill=1)

    # --------------------------------------------------------------- header
    top = PAGE_H - MARGIN
    logo = (company.get("logo_file_path") or "").strip()
    text_x = MARGIN
    if logo and Path(logo).exists():
        try:
            c.drawImage(logo, MARGIN, top - 46, width=54, height=54,
                        preserveAspectRatio=True, mask="auto")
            text_x = MARGIN + 66
        except Exception:  # noqa: BLE001 - a bad logo must not stop a quotation
            log.warning("could not draw logo %s", logo)

    c.setFillColor(_hex("#1a1a1a"))
    c.setFont("Helvetica-Bold", 23)
    c.drawString(text_x, top - 20, name)

    if tagline:
        c.setFillColor(accent)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(text_x, top - 34, tagline)

    reg = (company.get("registration_number") or "").strip()
    status = f"VAT Reg: {vat_no}" if charges_vat else "Not currently VAT registered"
    c.setFillColor(_hex("#666666"))
    c.setFont("Helvetica-Oblique", 7.8)
    c.drawString(text_x, top - 46, f"CIPC Reg: {reg}   |   {status}" if reg else status)

    # Contact block, right-aligned and clear of the spine.
    right = PAGE_W - SPINE_W - 14
    c.setFillColor(_hex("#333333"))
    c.setFont("Helvetica", 8.6)
    y = top - 6
    for line in (company.get("physical_address") or company.get("postal_address") or "").split(","):
        if line.strip():
            c.drawRightString(right, y, line.strip())
            y -= 11
    phone = company.get("standard_phone") or company.get("standard_cell")
    if phone:
        c.drawRightString(right, y - 4, f"Tel: {phone}")

    c.setStrokeColor(accent)
    c.setLineWidth(1.1)
    rule_y = top - 60
    c.line(MARGIN, rule_y, right, rule_y)

    # ---------------------------------------------------------------- panel
    panel_top = rule_y - PANEL_TOP_GAP
    panel_bottom = FOOTER_H + 16
    c.setStrokeColor(rule)
    c.setLineWidth(0.8)
    c.rect(MARGIN, panel_bottom, right - MARGIN, panel_top - panel_bottom,
           stroke=1, fill=0)

    inner_l = MARGIN + 20
    inner_r = right - 20
    inner_w = inner_r - inner_l
    y = panel_top - 26

    c.setFillColor(_hex("#333333"))
    c.setFont("Helvetica", 9)
    c.drawRightString(inner_r, y, date_text)
    y -= 26

    body = ParagraphStyle("body", fontName="Helvetica", fontSize=9,
                          leading=13.4, textColor=_hex("#222222"),
                          alignment=TA_JUSTIFY)
    strong = ParagraphStyle("strong", parent=body, fontName="Helvetica-Bold")

    def flow(text, style=body, gap=9.0):
        nonlocal y
        para = Paragraph(text, style)
        _, h = para.wrap(inner_w, 400)
        para.drawOn(c, inner_l, y - h)
        y -= h + gap

    if client.get("organisation"):
        flow(client["organisation"], strong, 3)
    if client.get("attention"):
        flow(f"Attention: {client['attention']}", body, 3)
    if client.get("email"):
        flow(f"Email: {client['email']}", body, 14)

    flow(f"RE: QUOTATION – {reference}" if reference else "RE: QUOTATION", strong, 3)
    if subject:
        flow(f'<font color="{pal["accent"]}">{subject.upper()}</font>', strong, 12)

    salutation = client.get("salutation") or "Dear Sir/Madam,"
    flow(salutation, body, 10)
    flow(
        "Thank you for the opportunity to submit a quotation in response to the "
        f"above-referenced request. {company.get('company_name', 'We')} is pleased "
        "to offer the following pricing, as detailed below.", body, 14)

    # ----------------------------------------------------------- line items
    head = ["No.", "Description", "Serial No.", "Qty", "Unit Price", "Total"]
    rows = [head]
    subtotal = 0.0
    any_tbc = False

    for i, item in enumerate(line_items, 1):
        qty = float(item.get("qty") or 1)
        unit = item.get("unit_price")
        if unit in (None, ""):
            any_tbc = True
            unit_cell, total_cell = "TBC", "TBC"
        else:
            line_total = qty * float(unit)
            subtotal += line_total
            unit_cell, total_cell = _money(unit), _money(line_total)
        rows.append([
            str(i),
            Paragraph(str(item.get("description", "")), body),
            str(item.get("serial") or "–"),
            f"{qty:g}",
            unit_cell,
            total_cell,
        ])

    total = subtotal * 1.15 if charges_vat else subtotal
    if charges_vat:
        rows.append(["", "", "", "", "Subtotal", _money(subtotal)])
        rows.append(["", "", "", "", "VAT (15%)", _money(subtotal * 0.15)])
    rows.append(["", "", "", "", "TOTAL", _money(total)])

    widths = [24, inner_w - 24 - 78 - 30 - 66 - 66, 78, 30, 66, 66]
    table = Table(rows, colWidths=widths, repeatRows=1)
    total_rows = len(rows)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), band),
        ("TEXTCOLOR", (0, 0), (-1, 0), on_band),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.4),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("TEXTCOLOR", (0, 1), (-1, -1), _hex("#222222")),
        ("GRID", (0, 0), (-1, -1), 0.5, rule),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("ALIGN", (4, 1), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (1, 0), (1, -1), 7),
        # The total row: tinted, heavier, and the only place the eye should stop.
        ("BACKGROUND", (0, total_rows - 1), (-1, total_rows - 1), tint),
        ("FONTNAME", (4, total_rows - 1), (-1, total_rows - 1), "Helvetica-Bold"),
        ("FONTSIZE", (4, total_rows - 1), (-1, total_rows - 1), 9.4),
        ("SPAN", (0, total_rows - 1), (3, total_rows - 1)),
    ]
    if charges_vat:
        for offset in (3, 2):
            style.append(("SPAN", (0, total_rows - offset), (3, total_rows - offset)))
    table.setStyle(TableStyle(style))

    _, table_h = table.wrap(inner_w, 500)
    table.drawOn(c, inner_l, y - table_h)
    y -= table_h + 16

    # ------------------------------------------------------------ the terms
    if location_text:
        flow(location_text, body, 8)
    if validity_text:
        flow(validity_text, body, 8)

    if charges_vat:
        vat_line = (f"All prices are quoted in South African Rand (ZAR) and are "
                    f"inclusive of VAT at 15%. VAT registration number {vat_no}.")
    else:
        vat_line = (f"All prices are quoted in South African Rand (ZAR). "
                    f"{company.get('company_name', 'The supplier')} is not currently a "
                    f"VAT-registered vendor; the prices quoted above are therefore "
                    f"all-inclusive of costs and no VAT is levied.")
    flow(vat_line, body, 8)

    if any_tbc:
        flow('<font color="#8a1c1c"><b>This quotation is incomplete.</b> Lines '
             'marked TBC have no price and are excluded from the total.</font>',
             body, 8)

    flow(closing_text or
         "We trust that this quotation meets your requirements and look forward to "
         "a favourable response. Should you require any further information, please "
         "do not hesitate to contact us.", body, 22)

    # -------------------------------------------------------- the signature
    flow("Yours faithfully,", body, 34)
    c.setStrokeColor(_hex("#555555"))
    c.setLineWidth(0.7)
    c.line(inner_l, y + 6, inner_l + 170, y + 6)
    y -= 4
    c.setFillColor(_hex("#111111"))
    c.setFont("Helvetica-Bold", 9.2)
    c.drawString(inner_l, y, company.get("authorized_signatory_name")
                 or company.get("standard_contact_person") or "")
    y -= 12
    c.setFont("Helvetica", 8.8)
    c.setFillColor(_hex("#333333"))
    capacity = company.get("authorized_signatory_capacity") or "Authorised Signatory"
    c.drawString(inner_l, y, f"{capacity}, {company.get('company_name', '')}")
    y -= 14
    c.drawString(inner_l, y, "Date: ______________________")

    # ----------------------------------------------------------- the footer
    c.setFillColor(band)
    path = c.beginPath()
    path.moveTo(MARGIN + 60, 0)
    path.lineTo(PAGE_W, 0)
    path.lineTo(PAGE_W, FOOTER_H)
    path.lineTo(MARGIN + 90, FOOTER_H)
    path.close()
    c.drawPath(path, stroke=0, fill=1)

    directors = (company.get("directors") or "").strip()
    bits = []
    if directors:
        bits.append(f"DIRECTORS: {directors.upper()}")
    if reg:
        bits.append(f"REGISTRATION NO {reg}")
    if bits:
        c.setFillColor(on_band)
        c.setFont("Helvetica-Bold", 7.8)
        c.drawCentredString((MARGIN + 90 + PAGE_W) / 2, FOOTER_H / 2 - 3,
                            "     ".join(bits))

    c.showPage()
    c.save()
    log.info("quotation rendered: %s", output_path.name)
    return output_path
