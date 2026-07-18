import sys
from pathlib import Path
sys.path.append("/Users/harry/Documents/Data set V2/tender_ml")
from models.pdf_parser import parse_tender_document

path = Path("tests/fixtures/rfb_001_comms.docx")
parsed = parse_tender_document(path)
print(parsed)
