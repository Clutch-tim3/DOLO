import sys

with open('models/pdf_parser.py', 'r') as f:
    content = f.read()

docx_logic = """import zipfile
import xml.etree.ElementTree as ET

def extract_text_from_docx(file_path: Path) -> str:
    try:
        with zipfile.ZipFile(str(file_path)) as docx:
            xml_content = docx.read('word/document.xml')
            tree = ET.XML(xml_content)
            WORD_NAMESPACE = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            PARA = WORD_NAMESPACE + 'p'
            TEXT = WORD_NAMESPACE + 't'
            
            paragraphs = []
            for paragraph in tree.iter(PARA):
                texts = [node.text for node in paragraph.iter(TEXT) if node.text]
                if texts:
                    paragraphs.append(''.join(texts))
            return '\\n'.join(paragraphs)
    except Exception as e:
        print(f"Error extracting text from DOCX: {e}")
        return ""

def extract_text_from_pdf(file_path: Path) -> str:
    \"\"\"Reads all text from a PDF or DOCX file.\"\"\"
    ext = str(file_path).lower()
    if ext.endswith('.docx'):
        return extract_text_from_docx(file_path)
        
    try:
        reader = PdfReader(str(file_path))
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\\n"
        return text
    except Exception as e:
        print(f"Error extracting text from PDF: {e}")
        return ""
"""

# Replace the original extract_text_from_pdf
old_extract = """def extract_text_from_pdf(file_path: Path) -> str:
    \"\"\"Reads all text from a PDF file.\"\"\"
    try:
        reader = PdfReader(str(file_path))
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\\n"
        return text
    except Exception as e:
        print(f"Error extracting text from PDF: {e}")
        return \"\"\""""

content = content.replace(old_extract, docx_logic)

with open('models/pdf_parser.py', 'w') as f:
    f.write(content)
