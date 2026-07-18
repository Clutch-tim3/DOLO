import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from models.pdf_parser import extract_text_from_pdf

text = extract_text_from_pdf("data/archive/document_2.pdf")
print(text)
