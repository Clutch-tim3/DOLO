import pytest
from hypothesis import given, strategies as st, settings
from unittest.mock import patch
from pathlib import Path
from models.pdf_parser import parse_tender_document

# This test generates purely random text to ensure the regex parser never crashes or hangs
@given(text=st.text())
@settings(deadline=None)
def test_parser_never_crashes_on_random_garbage(text):
    with patch("models.pdf_parser.extract_text_from_pdf", return_value=text):
        # We pass a dummy path because the extraction is mocked
        result = parse_tender_document(Path("dummy.pdf"))
        
        # It must return a dictionary with extraction completeness
        assert isinstance(result, dict)
        assert "extraction_completeness" in result

# Test edge cases where dates or numbers are wildly out of bounds or maliciously formatted
@given(
    huge_number=st.integers(min_value=1_000_000, max_value=10**20),
    random_padding=st.text(alphabet=" \n\t\r", min_size=0, max_size=100),
    invalid_date=st.dates()
)
@settings(deadline=None)
def test_parser_handles_extreme_numbers_and_dates(huge_number, random_padding, invalid_date):
    # Construct a string that looks somewhat like a tender document but with adversarial data
    adversarial_text = f"Tender Value: R {huge_number}{random_padding} Closing date: {invalid_date.strftime('%d %B %Y')}"
    
    with patch("models.pdf_parser.extract_text_from_pdf", return_value=adversarial_text):
        result = parse_tender_document(Path("dummy.pdf"))
        
        assert isinstance(result, dict)
        # Even if it parses the huge number, it shouldn't crash
        # If it fails to parse the date, deadline_days shouldn't cause a crash
        
# Test specific regex catastrophic backtracking boundaries
@given(spaces=st.integers(min_value=1, max_value=50000))
@settings(deadline=None)
def test_regex_catastrophic_backtracking_prevention(spaces):
    # If the regex uses .* or \s+ improperly, 50,000 spaces could hang the CPU
    adversarial_text = "specific goals" + (" " * spaces) + "HDI"
    
    with patch("models.pdf_parser.extract_text_from_pdf", return_value=adversarial_text):
        result = parse_tender_document(Path("dummy.pdf"))
        
        assert isinstance(result, dict)
        # We just want to ensure it completes in reasonable time (pytest will timeout if it hangs)
