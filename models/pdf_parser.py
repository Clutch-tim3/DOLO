import re
from pathlib import Path
from datetime import datetime
from pypdf import PdfReader

import zipfile
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
    """Reads all text from a PDF or DOCX file."""
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

def parse_company_pdf(file_path: Path) -> dict:
    """
    Parses a CIPC registration form or CSD report PDF to extract:
    - Company Name
    - Registration Number
    - CSD Supplier Number (MAAA number)
    - B-BBEE Level
    """
    text = extract_text_from_pdf(file_path)
    if not text:
        return {}
        
    results = {}
    
    # 1. Look for CSD Supplier Number (MAAA number)
    maaa_match = re.search(r'(MAAA[0-9]{7})', text, re.IGNORECASE)
    if maaa_match:
        results["supplier_number"] = maaa_match.group(1).upper()
        
    # 2. Look for B-BBEE Level
    # Pattern to match "B-BBEE Status Level of Contributor: Level X" or similar
    bbbee_match = re.search(r'(?:B-BBEE Status Level|B-BBEE Level|B-BBEE Status|BBBEE Status Level|BBBEE Level|B-BBEE Contributor Level)[:\s]+(?:of Contributor)?[:\s]*(?:Level\s+)?([1-9])', text, re.IGNORECASE)
    
    if not bbbee_match:
        bbbee_match = re.search(r'Level\s+([1-9])\s+(?:Contributor)?\s*(?:to)?\s*(?:B-BBEE|BBBEE)', text, re.IGNORECASE)
        
    if bbbee_match:
        results["bbbee_level"] = int(bbbee_match.group(1))
    else:
        # Try to infer from black ownership percentage (EME rules in CSD report)
        ownership_match = re.search(r'% Owned by black people[\s\r\n]+([0-9]{1,3}\.[0-9]{2})', text, re.IGNORECASE)
        if ownership_match:
            try:
                black_ownership = float(ownership_match.group(1))
                if black_ownership >= 100:
                    results["bbbee_level"] = 1
                elif black_ownership >= 51:
                    results["bbbee_level"] = 2
                else:
                    results["bbbee_level"] = 4
            except ValueError:
                pass
                
        if "bbbee_level" not in results and ("non-compliant" in text.lower() or "non compliant" in text.lower()):
            results["bbbee_level"] = 9
        
    # 3. Look for Registration Number (South Africa format: YYYY/XXXXXX/XX or KXXXXXXXXX)
    reg_match = re.search(r'(?:Enterprise Number|Registration Number|Registration No)[:\s]+([0-9]{4}/[0-9]{6}/[0-9]{2}|K[0-9]{9})', text, re.IGNORECASE)
    if not reg_match:
        # Fallback raw regex scan for the format
        reg_match = re.search(r'\b([0-9]{4}/[0-9]{6}/[0-9]{2}|K[0-9]{9})\b', text)
    if reg_match:
        results["registration_number"] = reg_match.group(1)
        
    # 4. Look for Company Name
    # Search for "Enterprise Name:" or "Legal Name:" or "Trading Name:"
    name_match = re.search(r'(?:Enterprise Name|Legal Name|Name of Company|Trading Name)[:\s]+([A-Z0-9\s,\(\)\.\-&]+)', text, re.IGNORECASE)
    if name_match:
        # Clean up the name
        cleaned_name = name_match.group(1).strip().split('\n')[0].strip()
        # Filter out obvious labels or too short names
        if len(cleaned_name) > 3 and not any(lbl in cleaned_name.lower() for lbl in ["registration", "postal", "physical", "enterprise"]):
            results["company_name"] = cleaned_name
            
    # Fallback to match company name by looking at lines in CIPC doc
    if "company_name" not in results:
        # COR14.3 certificates usually have the company name in a box or upper header
        # Let's search for "This is to certify that" ... "Enterprise Name:"
        for line in text.split('\n'):
            if "enterprise name" in line.lower() or "legal name" in line.lower():
                parts = re.split(r'[:\s]{2,}', line)
                if len(parts) > 1:
                    results["company_name"] = parts[-1].strip()
                    break
                    
    # Clean company name formatting
    if "company_name" in results:
        results["company_name"] = re.sub(r'\s+', ' ', results["company_name"]).strip().upper()
        
    # 5. Look for Pricing & Budget Heuristics (Tender Value, Bid Price, Lowest Price)
    # Extract all ZAR / Rand values from text
    price_matches = re.findall(r'(?:R|ZAR|\$)\s*([0-9][0-9\s,]{3,}(?:\.[0-9]{2})?)', text)
    cleaned_prices = []
    for p in price_matches:
        try:
            val = float(p.replace(" ", "").replace(",", ""))
            if val > 1000: # Ignore small change
                cleaned_prices.append(val)
        except:
            pass
            
    # Remove duplicates preserving order
    cleaned_prices = list(dict.fromkeys(cleaned_prices))
    
    # Try to find Bid Price or Tender Value via context
    # Look for "total price", "bid price", "contract sum", "tender sum"
    bid_price_match = re.search(r'(?:bid price|total price|contract sum|tender sum|tender price|offered price|pricing|amount)[:\s]*(?:R|ZAR|\$)?[:\s]*([0-9][0-9\s,]{3,}(?:\.[0-9]{2})?)', text, re.IGNORECASE)
    if bid_price_match:
        try:
            results["bid_price"] = float(bid_price_match.group(1).replace(" ", "").replace(",", ""))
        except:
            pass
            
    # Look for "estimated value", "tender value", "budget", "project estimate"
    est_value_match = re.search(r'(?:estimated value|tender value|budget|project estimate|estimated budget)[:\s]*(?:R|ZAR|\$)?[:\s]*([0-9][0-9\s,]{3,}(?:\.[0-9]{2})?)', text, re.IGNORECASE)
    if est_value_match:
        try:
            results["tender_value"] = float(est_value_match.group(1).replace(" ", "").replace(",", ""))
        except:
            pass

    # Heuristic assignments if not matched via context
    if cleaned_prices:
        # Usually, Tender Value > Bid Price
        sorted_prices = sorted(cleaned_prices)
        if "bid_price" not in results:
            # Assume the middle value or the lowest large value is the bid price
            results["bid_price"] = sorted_prices[0]
        if "tender_value" not in results:
            # Assume the largest value is the tender value
            results["tender_value"] = sorted_prices[-1] if sorted_prices[-1] > results["bid_price"] else results["bid_price"] * 1.2
            
        # Guess lowest competitor price: 90% of your bid price
        results["lowest_price"] = results["bid_price"] * 0.9
        
    return results

def parse_tender_document(file_path: Path) -> dict:
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
        extracted_fields.append('evaluation_system')

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
        extracted_fields.append('functionality_threshold_pct')
        
    # 4. Procedure and Supply Types
    if re.search(r'\b(?:request for quotation|rfq)\b', text, re.IGNORECASE):
        results['tender_proceduretype'] = 'RFQ'
        extracted_fields.append('tender_proceduretype')
    elif re.search(r'\b(?:request for proposal|rfp|open tender|invitation to bid)\b', text, re.IGNORECASE):
        results['tender_proceduretype'] = 'Open'
        extracted_fields.append('tender_proceduretype')
        
    if re.search(r'\b(?:services|consulting|maintenance)\b', text, re.IGNORECASE):
        results['tender_supplytype'] = 'Services'
        extracted_fields.append('tender_supplytype')
    elif re.search(r'\b(?:goods|supply and delivery|equipment)\b', text, re.IGNORECASE):
        results['tender_supplytype'] = 'Goods'
        extracted_fields.append('tender_supplytype')
    elif re.search(r'\b(?:works|construction|infrastructure)\b', text, re.IGNORECASE):
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
