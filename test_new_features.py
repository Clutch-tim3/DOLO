"""
test_new_features.py
====================
Verifies Workspace routes, document classification, and PDF quotation generation.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from models.pdf_parser import classify_document_type
from models.quotation_generator import generate_quotation_pdf

def test_document_classification():
    print("Testing Document Classification...")
    csd_text = "MAAA0012345 Central Supplier Database CSD report for Donington Vale"
    tax_text = "SARS Tax Compliance Status PIN Certificate Tax Clearance"
    bbbee_text = "Broad-Based Black Economic Empowerment B-BBEE Sworn Affidavit Level 1 EME"
    cidb_text = "Construction Industry Development Board CIDB CRS number 102938 Grade 5CE"

    res_csd = classify_document_type(csd_text)
    res_tax = classify_document_type(tax_text)
    res_bbbee = classify_document_type(bbbee_text)
    res_cidb = classify_document_type(cidb_text)

    assert res_csd["doc_type"] == "CSD_CERT", f"Expected CSD_CERT, got {res_csd}"
    assert res_tax["doc_type"] == "TAX_CLEARANCE", f"Expected TAX_CLEARANCE, got {res_tax}"
    assert res_bbbee["doc_type"] == "BBBEE_CERT", f"Expected BBBEE_CERT, got {res_bbbee}"
    assert res_cidb["doc_type"] == "CIDB_CERT", f"Expected CIDB_CERT, got {res_cidb}"

    print("  [OK] Document Classification PASSED")

def test_quotation_pdf_generation():
    print("Testing PDF Quotation Generation...")
    supplier_info = {
        "company_name": "DONINGTON VALE",
        "registration_number": "2023/100201/07",
        "csd_number": "MAAA0012345",
        "bbbee_level": 1,
        "cidb_grade": "5GB"
    }
    line_items = [
        {"description": "IT Infrastructure Deployment & Modernization", "qty": 1, "unit_price": 798116.25},
        {"description": "Enterprise Security Audit & SLA", "qty": 1, "unit_price": 50000.00}
    ]
    out_path = PROJECT_ROOT / "static" / "generated_quotations" / "test_quotation.pdf"
    
    res_path = generate_quotation_pdf(
        supplier_info=supplier_info,
        tender_title="Test Procurement Tender 1d59478c",
        line_items=line_items,
        output_path=out_path
    )
    
    assert res_path.exists(), "Quotation PDF file was not created!"
    assert res_path.stat().st_size > 1000, "PDF file is too small or corrupted!"
    print(f"  [OK] PDF Quotation generated successfully: {res_path} ({res_path.stat().st_size} bytes)")

if __name__ == "__main__":
    test_document_classification()
    test_quotation_pdf_generation()
    print("\nAll new feature tests PASSED!")
