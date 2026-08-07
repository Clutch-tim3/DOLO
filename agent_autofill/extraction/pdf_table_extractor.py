"""
Table-aware blank detection for PDFs.

The coordinate-based detector in layout_blank_extractor finds blanks drawn as
runs of underscores or dots. It cannot see a blank that is simply an empty
table cell, because there are no characters there to find — and SA bid forms
put their most important field in exactly that shape. Page 7 of the reference
tender has "NAME OF BIDDER" as a table cell whose answer belongs in the cell
beside it, with nothing on the line at all. That field was being missed
entirely, which on a real submission means the bidder's name is silently left
blank.

Same two layouts as the DOCX path, so the shapes are recognised consistently:
  * "label | blank" rows
  * column-headed entry grids
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber


@dataclass
class TableBlank:
    page_number: int
    label_text: str
    bbox: tuple[float, float, float, float] | None
    row_index: int
    col_index: int
    table_index: int
    shape: str                 # "label_value" | "column_header"
    confidence: float = 0.80
    #: The most recent heading above this cell. Load-bearing: MBD 1 carries the
    #: SAME labels twice — CONTACT PERSON, TELEPHONE NUMBER and E-MAIL ADDRESS
    #: appear under "BIDDING PROCEDURE ENQUIRIES MAY BE DIRECTED TO" (the
    #: buying institution's own staff) and again under "SUPPLIER INFORMATION"
    #: (us). Without the heading the two are indistinguishable, and the engine
    #: would write our contact details into the municipality's block.
    section: str = ""


def _clean(cell) -> str:
    return (cell or "").replace("\n", " ").strip()


def _looks_like_header_row(row: list[str]) -> bool:
    return bool(row) and all(c for c in row) and not all(len(c) > 60 for c in row)


def _has_blank_below(rows: list[list[str]]) -> bool:
    return any(any(not c for c in r) for r in rows[1:])


#: A row that is one populated cell spanning the width is a section heading.
def _section_heading(row: list[str]) -> str | None:
    filled = [c for c in row if c]
    if len(filled) != 1:
        return None
    text = filled[0]
    if len(text) < 4 or len(text) > 90:
        return None
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        return text
    return None


def extract_pdf_table_blanks(path: str | Path,
                             pages: list[int] | None = None) -> list[TableBlank]:
    """
    Find empty table cells whose label can be identified, either from the cell
    to their left or from their column heading.

    Page numbers are 1-based, matching how a person refers to a page.
    """
    out: list[TableBlank] = []
    with pdfplumber.open(str(path)) as pdf:
        targets = (
            [(n, pdf.pages[n - 1]) for n in pages if 0 < n <= len(pdf.pages)]
            if pages else list(enumerate(pdf.pages, start=1))
        )
        for page_no, page in targets:
            try:
                tables = page.extract_tables()
            except Exception:
                continue                      # a malformed table is not fatal
            for t_i, raw in enumerate(tables):
                rows = [[_clean(c) for c in row] for row in (raw or []) if row]
                if len(rows) < 1:
                    continue

                # Column-headed entry grid
                if len(rows) > 1 and _looks_like_header_row(rows[0]) and _has_blank_below(rows):
                    headers = rows[0]
                    for r_i, row in enumerate(rows[1:], start=1):
                        for c_i, cell in enumerate(row):
                            if cell:
                                continue
                            label = headers[c_i] if c_i < len(headers) else ""
                            if len(label) < 3:
                                continue
                            out.append(TableBlank(
                                page_number=page_no, label_text=label, bbox=None,
                                row_index=r_i, col_index=c_i, table_index=t_i,
                                shape="column_header",
                            ))
                    continue

                # "label | blank" rows
                section = ""
                for r_i, row in enumerate(rows):
                    heading = _section_heading(row)
                    if heading:
                        section = heading
                        continue
                    for c_i in range(len(row) - 1):
                        label, nxt = row[c_i], row[c_i + 1]
                        if len(label) < 3 or nxt:
                            continue
                        if not any(ch.isalpha() for ch in label):
                            continue
                        out.append(TableBlank(
                            page_number=page_no, label_text=label, bbox=None,
                            row_index=r_i, col_index=c_i + 1, table_index=t_i,
                            shape="label_value", section=section,
                        ))
    return out
