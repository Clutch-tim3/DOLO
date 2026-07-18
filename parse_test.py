import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from models.pdf_parser import extract_text_from_pdf

for f in Path("data/archive").glob("*.pdf"):
    print(f"--- {f.name} ---")
    text = extract_text_from_pdf(f)
    if "B-BBEE" in text or "BBEE" in text or "Level" in text or "LEVEL" in text or "b-bbee" in text.lower():
        lines = text.split("\n")
        for line in lines:
            if "bbbee" in line.lower() or "level" in line.lower():
                print(line)
