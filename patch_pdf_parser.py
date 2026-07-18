import re

with open('models/pdf_parser.py', 'r') as f:
    content = f.read()

new_logic = """
    # 1. Look for briefing date
    briefing_match = re.search(r'(?:briefing session|site meeting|compulsory briefing).*?([0-9]{1,2}\s+[a-zA-Z]{3,9}\s+20[2-3][0-9]|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/20[2-3][0-9])', text, re.IGNORECASE | re.DOTALL)
    if briefing_match:
        results["briefing_date"] = briefing_match.group(1).strip()
        
    # 2. Look for closing date
    closing_match = re.search(r'(?:closing date|deadline|submission date).*?([0-9]{1,2}\s+[a-zA-Z]{3,9}\s+20[2-3][0-9]|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/20[2-3][0-9])', text, re.IGNORECASE | re.DOTALL)
    if closing_match:
        results["closing_date"] = closing_match.group(1).strip()
        
    # 3. Look for award date / validity
    award_match = re.search(r'(?:validity period|award date).*?([0-9]{1,2}\s+[a-zA-Z]{3,9}\s+20[2-3][0-9]|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/20[2-3][0-9])', text, re.IGNORECASE | re.DOTALL)
    if award_match:
        results["award_date"] = award_match.group(1).strip()
"""

# Inject before return results in parse_tender_document
# Wait, parse_tender_document currently just returns a mock: 
# return {'confidence': 'low', 'evaluation_system': '80/20', 'specific_goals_bbbee_ratio': 1.0}
# Let's replace the whole parse_tender_document to include this logic safely.

target_func = """def parse_tender_document(file_path: Path) -> dict:
    text = extract_text_from_pdf(file_path)
    if not text:
        return {'confidence': 'low', 'evaluation_system': '80/20', 'specific_goals_bbbee_ratio': 1.0}"""

new_func = """def parse_tender_document(file_path: Path) -> dict:
    text = extract_text_from_pdf(file_path)
    if not text:
        return {'confidence': 'low', 'evaluation_system': '80/20', 'specific_goals_bbbee_ratio': 1.0}
        
    results = {'confidence': 'low', 'evaluation_system': '80/20', 'specific_goals_bbbee_ratio': 1.0}
    
    # Briefing
    briefing_match = re.search(r'(?:briefing session|site meeting|compulsory briefing).*?([0-9]{1,2}\s+[a-zA-Z]{3,9}\s+20[2-3][0-9]|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/20[2-3][0-9])', text, re.IGNORECASE)
    if briefing_match:
        results["briefing_date"] = briefing_match.group(1).strip()
        
    # Closing
    closing_match = re.search(r'(?:closing date|deadline|submission date).*?([0-9]{1,2}\s+[a-zA-Z]{3,9}\s+20[2-3][0-9]|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/20[2-3][0-9])', text, re.IGNORECASE)
    if closing_match:
        results["closing_date"] = closing_match.group(1).strip()
        
    # Award
    award_match = re.search(r'(?:validity period|award date).*?([0-9]{1,2}\s+[a-zA-Z]{3,9}\s+20[2-3][0-9]|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/20[2-3][0-9])', text, re.IGNORECASE)
    if award_match:
        results["award_date"] = award_match.group(1).strip()
        
    return results"""

# Need to strip out whatever was there before and replace it.
content = re.sub(r'def parse_tender_document.*?return.*?\n(?=\S|$)', new_func + '\n\n', content, flags=re.DOTALL)

with open('models/pdf_parser.py', 'w') as f:
    f.write(content)
