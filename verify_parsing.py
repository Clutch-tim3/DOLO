from models.pdf_parser import parse_tender_document
import os

files = ["lv_cabling_tender.pdf", "rfb_001_comms.docx"]
for f in files:
    path = os.path.join("tests", "fixtures", f)
    parsed = parse_tender_document(path)
    print(f"Parsed {f}:")
    print(parsed)
