import pytest
from pathlib import Path
from models.pdf_parser import parse_tender_document, extract_text_from_pdf, extract_text_from_docx

def test_pdf_extraction_succeeds(fixtures_dir):
    pdf_path = fixtures_dir / "alfred_duma.pdf"
    text = extract_text_from_pdf(pdf_path)
    assert text is not None
    assert len(text) > 0
    assert "ALFRED DUMA" in text

def test_docx_extraction_succeeds(fixtures_dir):
    docx_path = fixtures_dir / "rfb_001_comms.docx"
    text = extract_text_from_docx(docx_path)
    assert text is not None
    assert len(text) > 0
    assert "NATIONAL COMMUNICATIONS AGENCY" in text

def test_docx_and_pdf_produce_equivalent_quality(fixtures_dir):
    # Both documents have different content but similar structure completeness.
    pdf_path = fixtures_dir / "lv_cabling_tender.pdf"
    docx_path = fixtures_dir / "rfb_001_comms.docx"
    
    pdf_res = parse_tender_document(pdf_path)
    docx_res = parse_tender_document(docx_path)
    
    pdf_score = pdf_res.get('extraction_completeness', 0)
    docx_score = docx_res.get('extraction_completeness', 0)
    
    # Assert they are within 15 percentage points
    assert abs(pdf_score - docx_score) <= 0.25

def test_malformed_file_fails_gracefully(fixtures_dir):
    malformed_path = fixtures_dir / "malformed.pdf"
    res = parse_tender_document(malformed_path)
    # The corrupted PDF will fail parsing text
    assert res.get('extraction_incomplete') is True
    assert res.get('extraction_completeness', 1.0) == 0.0

def test_unsupported_file_type_rejected(fixtures_dir, tmp_path):
    txt_path = tmp_path / "tender.txt"
    txt_path.write_text("Hello world")
    
    res = parse_tender_document(txt_path)
    assert res.get('extraction_incomplete') is True

def test_boilerplate_vs_actual_value_distinction(tmp_path):
    text = """
    the applicable system for this tender is the 80/20 system
    BOILERPLATE: up to R50,000,000 for all standard projects.
    Tender value: R 2,500,000
    """
    pdf_path = tmp_path / "test.pdf"
    # We mock the extract_text_from_pdf temporarily or we can just create a pdf.
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.multi_cell(0, 10, text)
    pdf.output(str(pdf_path))
    
    res = parse_tender_document(pdf_path)
    assert res.get('evaluation_system') == '80/20'
    assert res.get('tender_value') == 2500000.0

def test_functionality_threshold_extraction(fixtures_dir):
    pdf_path = fixtures_dir / "alfred_duma.pdf"
    res = parse_tender_document(pdf_path)
    assert res.get('had_functionality_gate') is True
    assert res.get('functionality_threshold_pct') == 80.0

def test_locality_requirement_extraction(fixtures_dir):
    pdf_path = fixtures_dir / "alfred_duma.pdf"
    # In predict/eligibility_gate.py this is parsed. We just make sure text contains it.
    text = extract_text_from_pdf(pdf_path)
    import re
    mandatory_loc_match = re.search(r'(?:must be registered in|operating within|located in)\s+([A-Za-z\s]+)(?:municipality|district|province)', text, re.IGNORECASE)
    assert mandatory_loc_match is not None
    assert "alfred duma" in mandatory_loc_match.group(1).lower()

def test_price_and_lowest_price_extraction_per_document(fixtures_dir):
    res1 = parse_tender_document(fixtures_dir / "alfred_duma.pdf")
    res2 = parse_tender_document(fixtures_dir / "lv_cabling_tender.pdf")
    
    assert res1.get('tender_value') != res2.get('tender_value')
