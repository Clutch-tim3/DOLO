import sys
import re
from datetime import datetime

with open('models/pdf_parser.py', 'r') as f:
    content = f.read()

# We need to add imports to pdf_parser
if "from datetime import datetime" not in content:
    content = content.replace("from pathlib import Path\n", "from pathlib import Path\nfrom datetime import datetime\n")

# Re-write the parse_tender_document logic
# The best way is to replace the entire parse_tender_document function.
import ast
# To accurately replace it, I will use regex or string replace from def parse_tender_document up to the end of the file.

# Find the start of parse_tender_document
start_idx = content.find('def parse_tender_document(')
end_idx = len(content)

new_parser = '''def parse_tender_document(file_path: Path) -> dict:
    text = extract_text_from_pdf(file_path)
    if not text:
        return {'extraction_incomplete': True, 'extraction_completeness': 0.0, 'missing_fields': ['All text unreadable']}
        
    results = {'confidence': 'medium', 'specific_goals_bbbee_ratio': 1.0}
    
    expected_fields = [
        'evaluation_system', 'tender_value', 'bid_price', 
        'deadline_days', 'tender_proceduretype', 'tender_supplytype',
        'functionality_threshold_pct'
    ]
    extracted_fields = []
    
    # 1. Dates and Deadlines
    briefing_match = re.search(r'(?:briefing session|site meeting|compulsory briefing).*?([0-9]{1,2}\s+[a-zA-Z]{3,9}\s+20[2-3][0-9]|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/20[2-3][0-9])', text, re.IGNORECASE)
    if briefing_match:
        results['briefing_date'] = briefing_match.group(1).strip()
        
    closing_match = re.search(r'(?:closing date|deadline|submission date).*?([0-9]{1,2}\s+[a-zA-Z]{3,9}\s+20[2-3][0-9]|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/20[2-3][0-9])', text, re.IGNORECASE)
    if closing_match:
        closing_str = closing_match.group(1).strip()
        results['closing_date'] = closing_str
        
        # Try to calculate deadline_days
        try:
            # Very basic parsing for common SA formats
            parsed_date = None
            if '-' in closing_str:
                parsed_date = datetime.strptime(closing_str, "%Y-%m-%d")
            elif '/' in closing_str:
                parts = closing_str.split('/')
                if len(parts[2]) == 4:
                    parsed_date = datetime.strptime(closing_str, "%d/%m/%Y")
            else:
                # E.g. "15 August 2026"
                for fmt in ["%d %B %Y", "%d %b %Y"]:
                    try: parsed_date = datetime.strptime(closing_str, fmt); break
                    except: pass
                    
            if parsed_date:
                days = (parsed_date - datetime.now()).days
                results['deadline_days'] = max(0, days)
                extracted_fields.append('deadline_days')
        except:
            pass

    award_match = re.search(r'(?:validity period|award date).*?([0-9]{1,2}\s+[a-zA-Z]{3,9}\s+20[2-3][0-9]|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/20[2-3][0-9])', text, re.IGNORECASE)
    if award_match:
        results['award_date'] = award_match.group(1).strip()
        
    # 2. Preference system detection
    eval_sys_match = re.search(r'(?:applicable\s+preference\s+point\s+system|system\s+for\s+this\s+tender\s+is|will\s+be\s+evaluated\s+on\s+the)[\s\w]*(80/20|90/10)', text, re.IGNORECASE)
    if eval_sys_match:
        results['evaluation_system'] = eval_sys_match.group(1)
        results['low_confidence_evaluation_system'] = False
        extracted_fields.append('evaluation_system')
    else:
        results['evaluation_system'] = '80/20'
        results['low_confidence_evaluation_system'] = True

    # Specific goals vs B-BBEE
    if "specific goals" in text.lower():
        goals_section = re.search(r'specific goals.{0,500}', text, re.IGNORECASE | re.DOTALL)
        if goals_section:
            section_text = goals_section.group(0).lower()
            if "hdi" in section_text or "locality" in section_text or "rdp" in section_text or "women" in section_text:
                results['specific_goals_bbbee_ratio'] = 0.5
            else:
                results['specific_goals_bbbee_ratio'] = 1.0
                
    # 3. Functionality/pre-qualification
    func_match = re.search(r'(?:minimum|must score|score at least|score of|minimum qualifying score of|functionality).*?([0-9]{2})(?:\s*%|\s+points)', text, re.IGNORECASE)
    if func_match:
        results['functionality_threshold_pct'] = float(func_match.group(1))
        results['had_functionality_gate'] = True
        extracted_fields.append('functionality_threshold_pct')
    else:
        results['had_functionality_gate'] = False
        results['functionality_threshold_pct'] = None
        
    # 4. Procedure and Supply Types
    if re.search(r'\\b(?:request for quotation|rfq)\\b', text, re.IGNORECASE):
        results['tender_proceduretype'] = 'RFQ'
        extracted_fields.append('tender_proceduretype')
    elif re.search(r'\\b(?:request for proposal|rfp|open tender|invitation to bid)\\b', text, re.IGNORECASE):
        results['tender_proceduretype'] = 'Open'
        extracted_fields.append('tender_proceduretype')
        
    if re.search(r'\\b(?:services|consulting|maintenance)\\b', text, re.IGNORECASE):
        results['tender_supplytype'] = 'Services'
        extracted_fields.append('tender_supplytype')
    elif re.search(r'\\b(?:goods|supply and delivery|equipment)\\b', text, re.IGNORECASE):
        results['tender_supplytype'] = 'Goods'
        extracted_fields.append('tender_supplytype')
    elif re.search(r'\\b(?:works|construction|infrastructure)\\b', text, re.IGNORECASE):
        results['tender_supplytype'] = 'Works'
        extracted_fields.append('tender_supplytype')

    # 5. Look for Pricing & Budget Heuristics (Excluding 50,000,000 boilerplate)
    price_matches = re.findall(r'(?:R|ZAR|\$)\s*([0-9][0-9\s,]{3,}(?:\.[0-9]{2})?)', text)
    cleaned_prices = []
    for p in price_matches:
        try:
            val = float(p.replace(" ", "").replace(",", ""))
            # Ignore R50m which is often boilerplate for preference systems
            if val > 1000 and val != 50000000.0:
                cleaned_prices.append(val)
        except:
            pass
            
    cleaned_prices = list(dict.fromkeys(cleaned_prices))
    
    bid_price_match = re.search(r'(?:bid price|total price|contract sum|tender sum|tender price|offered price|pricing|amount)[:\s]*(?:R|ZAR|\$)?[:\s]*([0-9][0-9\s,]{3,}(?:\.[0-9]{2})?)', text, re.IGNORECASE)
    if bid_price_match:
        try:
            val = float(bid_price_match.group(1).replace(" ", "").replace(",", ""))
            if val != 50000000.0: results["bid_price"] = val
        except:
            pass
            
    est_value_match = re.search(r'(?:estimated value|tender value|budget|project estimate|estimated budget)[:\s]*(?:R|ZAR|\$)?[:\s]*([0-9][0-9\s,]{3,}(?:\.[0-9]{2})?)', text, re.IGNORECASE)
    if est_value_match:
        try:
            val = float(est_value_match.group(1).replace(" ", "").replace(",", ""))
            if val != 50000000.0: results["tender_value"] = val
        except:
            pass

    if cleaned_prices:
        sorted_prices = sorted(cleaned_prices)
        if "bid_price" not in results and len(sorted_prices) > 0:
            results["bid_price"] = sorted_prices[0]
        if "tender_value" not in results and len(sorted_prices) > 0:
            results["tender_value"] = sorted_prices[-1] if sorted_prices[-1] > results["bid_price"] else results["bid_price"] * 1.2
            
    if "bid_price" in results:
        results["lowest_price"] = results["bid_price"] * 0.9
        extracted_fields.append('bid_price')
    if "tender_value" in results:
        extracted_fields.append('tender_value')

    # Convert ZAR to USD for the ML model
    ZAR_TO_USD = 18.5
    if "bid_price" in results:
        results["bid_priceUsd"] = results["bid_price"] / ZAR_TO_USD
    if "tender_value" in results:
        results["tender_estimatedpriceUsd"] = results["tender_value"] / ZAR_TO_USD
        
    results["tender_description_length"] = len(text)

    # 6. Extraction Completeness Scoring
    completeness = len(extracted_fields) / len(expected_fields)
    results["extraction_completeness"] = completeness
    
    missing = [f for f in expected_fields if f not in extracted_fields]
    results["missing_fields"] = missing

    # Set extraction_incomplete if below 50% or missing critical financial fields
    if completeness < 0.50 or "tender_value" not in results or "bid_price" not in results:
        results["extraction_incomplete"] = True
    else:
        results["extraction_incomplete"] = False

    return results
'''

content = content[:start_idx] + new_parser

with open('models/pdf_parser.py', 'w') as f:
    f.write(content)
