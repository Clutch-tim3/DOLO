import sys

with open('models/pdf_parser.py', 'r') as f:
    content = f.read()

# 1. Remove the rogue 'return results' and the redefining of 'results' after it in parse_tender_document
rogue_return = """    if award_match:
        results['award_date'] = award_match.group(1).strip()
        
    return results
        
    results = {'confidence': 'medium', 'specific_goals_bbbee_ratio': 1.0}"""

fixed_return = """    if award_match:
        results['award_date'] = award_match.group(1).strip()
        
    results['confidence'] = 'medium'"""

content = content.replace(rogue_return, fixed_return)

# 2. Add the pricing extraction logic to parse_tender_document right before returning
pricing_logic = """
    # 4. Look for Pricing & Budget Heuristics (Tender Value, Bid Price, Lowest Price)
    price_matches = re.findall(r'(?:R|ZAR|\$)\s*([0-9][0-9\s,]{3,}(?:\.[0-9]{2})?)', text)
    cleaned_prices = []
    for p in price_matches:
        try:
            val = float(p.replace(" ", "").replace(",", ""))
            if val > 1000:
                cleaned_prices.append(val)
        except:
            pass
            
    cleaned_prices = list(dict.fromkeys(cleaned_prices))
    
    bid_price_match = re.search(r'(?:bid price|total price|contract sum|tender sum|tender price|offered price|pricing|amount)[:\s]*(?:R|ZAR|\$)?[:\s]*([0-9][0-9\s,]{3,}(?:\.[0-9]{2})?)', text, re.IGNORECASE)
    if bid_price_match:
        try:
            results["bid_price"] = float(bid_price_match.group(1).replace(" ", "").replace(",", ""))
        except:
            pass
            
    est_value_match = re.search(r'(?:estimated value|tender value|budget|project estimate|estimated budget)[:\s]*(?:R|ZAR|\$)?[:\s]*([0-9][0-9\s,]{3,}(?:\.[0-9]{2})?)', text, re.IGNORECASE)
    if est_value_match:
        try:
            results["tender_value"] = float(est_value_match.group(1).replace(" ", "").replace(",", ""))
        except:
            pass

    if cleaned_prices:
        sorted_prices = sorted(cleaned_prices)
        if "bid_price" not in results:
            results["bid_price"] = sorted_prices[0]
        if "tender_value" not in results:
            results["tender_value"] = sorted_prices[-1] if sorted_prices[-1] > results["bid_price"] else results["bid_price"] * 1.2
            
        results["lowest_price"] = results["bid_price"] * 0.9

    # 5. Check if extraction is incomplete
    if "bid_price" not in results or "tender_value" not in results:
        results["extraction_incomplete"] = True
    else:
        results["extraction_incomplete"] = False

    return results
"""

# Replace the very last return results in pdf_parser
last_return = "    return results\n"
if content.endswith(last_return):
    content = content[:-len(last_return)] + pricing_logic

with open('models/pdf_parser.py', 'w') as f:
    f.write(content)

# Now patch app.py to handle extraction_incomplete
with open('app.py', 'r') as f:
    app_content = f.read()

# For the batch job loop:
batch_extract = """        parsed_tender = parse_tender_document(file_path)
        
        try:
            supplier_price = parsed_tender.get("bid_price", 1000000.0)
            lowest_price = parsed_tender.get("lowest_price", supplier_price * 0.9)
            tender_value = parsed_tender.get("tender_value")
            eval_system = parsed_tender.get("evaluation_system", "80/20")
            sg_ratio = parsed_tender.get("specific_goals_bbbee_ratio", 1.0)"""

new_batch_extract = """        parsed_tender = parse_tender_document(file_path)
        
        if parsed_tender.get("extraction_incomplete"):
            job["processed"] += 1
            job["results"].append({
                "prediction_id": str(uuid.uuid4()),
                "filename": filename,
                "tender_identifier": tender_id,
                "disqualified": False,
                "hard_failures": [],
                "win_probability": None,
                "sa_adjusted_probability": None,
                "recommendation": "ERROR",
                "competitive_position": None,
                "parsed_tender_value": None,
                "preferential_framework": parsed_tender.get("evaluation_system", "Unknown"),
                "processing_error": "Failed to extract required financial parameters (tender_value, bid_price) from document."
            })
            continue

        try:
            supplier_price = parsed_tender.get("bid_price")
            lowest_price = parsed_tender.get("lowest_price")
            tender_value = parsed_tender.get("tender_value")
            eval_system = parsed_tender.get("evaluation_system", "80/20")
            sg_ratio = parsed_tender.get("specific_goals_bbbee_ratio", 1.0)"""

app_content = app_content.replace(batch_extract, new_batch_extract)

# Remove the fallback assignment for tender_value since it will never hit if extracted successfully
fallback = """            if tender_value is None:
                tender_value = supplier_price * 1.5"""
app_content = app_content.replace(fallback, "")

# For the single endpoint:
single_extract = """    parsed_tender = parse_tender_document(temp_pdf_path)
    
    # Run eligibility
    eligibility_result = run_eligibility_gate(
        parsed_tender=parsed_tender,
        company_profile=matched_company
    )
    
    if not eligibility_result["is_eligible"]:
        return {
            "supplier": name_to_use,
            "matched_from_archive": matched_company is not None,
            "recommendation": "DISQUALIFIED",
            "hard_failures": eligibility_result["hard_failures"]
        }"""

new_single_extract = """    parsed_tender = parse_tender_document(temp_pdf_path)
    
    if parsed_tender.get("extraction_incomplete"):
        raise HTTPException(status_code=400, detail="Failed to extract required financial parameters (tender_value, bid_price) from document.")
        
    # Run eligibility
    eligibility_result = run_eligibility_gate(
        parsed_tender=parsed_tender,
        company_profile=matched_company
    )
    
    if not eligibility_result["is_eligible"]:
        return {
            "supplier": name_to_use,
            "matched_from_archive": matched_company is not None,
            "recommendation": "DISQUALIFIED",
            "hard_failures": eligibility_result["hard_failures"]
        }"""
app_content = app_content.replace(single_extract, new_single_extract)

# And remove fallback from single endpoint too
single_fallback = """    if tender_value is None:
        tender_value = supplier_price * 1.5"""
app_content = app_content.replace(single_fallback, "")

single_pricing = """    supplier_price = parsed_tender.get("bid_price", 1000000.0)
    lowest_price = parsed_tender.get("lowest_price", supplier_price * 0.9)
    tender_value = parsed_tender.get("tender_value")"""
new_single_pricing = """    supplier_price = parsed_tender.get("bid_price")
    lowest_price = parsed_tender.get("lowest_price")
    tender_value = parsed_tender.get("tender_value")"""
app_content = app_content.replace(single_pricing, new_single_pricing)


with open('app.py', 'w') as f:
    f.write(app_content)
