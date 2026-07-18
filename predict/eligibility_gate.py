import re

def check_hard_eligibility(tender_text: str, supplier_profile: dict) -> dict:
    """
    Evaluates hard eligibility rules before ML inference.
    
    Returns:
    {
      'eligible': bool,
      'hard_failures': [ {check, reason} ],
      'logistics_warnings': [ {check, reason} ],
      'max_achievable_functionality_pct': float or None,
      'confidence': 'high'/'medium'/'low'
    }
    """
    hard_failures = []
    logistics_warnings = []
    max_achievable_functionality_pct = None
    confidence = 'high'
    
    if not tender_text:
        return {
            'eligible': True,
            'hard_failures': [],
            'logistics_warnings': [],
            'max_achievable_functionality_pct': None,
            'confidence': 'low'
        }

    # CHECK 1: Functionality threshold feasibility
    min_thresh_match = re.search(r'(?:minimum|must score|score at least|score of|minimum qualifying score of|functionality).*?([0-9]{2})(?:\s*%|\s+points)', tender_text, re.IGNORECASE)
    required_pass_percentage = None
    if min_thresh_match:
        required_pass_percentage = float(min_thresh_match.group(1))

    history_points = 0
    history_keywords = [r"past contract", r"reference letter", r"orders", r"appointment letter", r"similar nature", r"proof of", r"experience", r"track record", r"previous"]
    lines = tender_text.split('\n')
    
    in_functionality_section = False
    for line in lines:
        lower_line = line.lower()
        if "functionality" in lower_line or "evaluation criteria" in lower_line or "scorecard" in lower_line:
            in_functionality_section = True
        
        if in_functionality_section and any(re.search(kw, lower_line) for kw in history_keywords):
            pts_match = re.search(r'(?:points|weight|max|maximum)[\:\s]*([0-9]{1,3})', lower_line)
            if not pts_match:
                pts_match = re.search(r'\b([0-9]{1,3})\s*$', lower_line)
            if pts_match:
                try:
                    val = float(pts_match.group(1))
                    if val <= 100: # sanity check
                        history_points += val
                except:
                    pass

    total_max_score = 100
    if required_pass_percentage is not None:
        max_achievable = total_max_score - history_points
        max_achievable_functionality_pct = (max_achievable / total_max_score) * 100
        
        supplier_history = supplier_profile.get('pit_total_wins', 0)
        
        if max_achievable_functionality_pct < required_pass_percentage and supplier_history == 0:
            hard_failures.append({
                "check": "Functionality threshold feasibility",
                "reason": f"Functionality gate requires {required_pass_percentage}% but supplier with zero tender history can achieve max {max_achievable_functionality_pct}%",
                "points_lost": history_points
            })

    # CHECK 2: Geographic / locality eligibility
    supplier_prov = supplier_profile.get('province', '').lower()
    supplier_muni = supplier_profile.get('registered_municipality', '').lower()
    
    mandatory_loc_match = re.search(r'(?:must be registered in|operating within|located in)\s+([A-Za-z\s]+)(?:municipality|district|province)', tender_text, re.IGNORECASE)
    if mandatory_loc_match:
        required_loc = mandatory_loc_match.group(1).strip().lower()
        if required_loc and supplier_muni and supplier_prov:
            if (supplier_muni not in required_loc and required_loc not in supplier_muni) and (supplier_prov not in required_loc and required_loc not in supplier_prov):
                hard_failures.append({
                    "check": "Geographic / locality eligibility",
                    "reason": f"Tender requires location in {required_loc.title()}, but supplier is in {supplier_muni.title()}/{supplier_prov.title()}.",
                    "points_lost": None
                })

    # CHECK 3: Mandatory registration/certification gates
    mandatory_csd = re.search(r'(?:must be registered|compulsory|mandatory|will not be considered).*?(?:csd|central supplier database)', tender_text, re.IGNORECASE)
    mandatory_cidb = re.search(r'(?:must be registered|compulsory|mandatory|will not be considered).*?(?:cidb)', tender_text, re.IGNORECASE)
    mandatory_tax = re.search(r'(?:must provide|compulsory|mandatory|will not be considered).*?(?:tax clearance|sars pin)', tender_text, re.IGNORECASE)
    
    if mandatory_csd and not supplier_profile.get('has_csd', True):
        hard_failures.append({"check": "Mandatory Registration", "reason": "CSD Registration is mandatory but missing.", "points_lost": None})
    if mandatory_cidb and not supplier_profile.get('has_cidb', True):
        hard_failures.append({"check": "Mandatory Registration", "reason": "CIDB Registration is mandatory but missing.", "points_lost": None})
    if mandatory_tax and not supplier_profile.get('has_tax_clearance', True):
        hard_failures.append({"check": "Mandatory Registration", "reason": "Valid Tax Clearance is mandatory but missing.", "points_lost": None})

    # CHECK 4: Compulsory briefing/site meeting feasibility
    briefing_match = re.search(r'compulsory (?:briefing|site meeting)', tender_text, re.IGNORECASE)
    if briefing_match and supplier_prov:
        provinces = ["gauteng", "kwazulu-natal", "western cape", "eastern cape", "free state", "mpumalanga", "limpopo", "north west", "northern cape"]
        tender_provs = [p for p in provinces if p in tender_text.lower()]
        if tender_provs and supplier_prov not in tender_provs:
            logistics_warnings.append({
                "check": "Compulsory briefing logistics",
                "reason": f"Compulsory briefing is likely in {tender_provs[0].title()}, >300km from {supplier_prov.title()}."
            })
            
    # CHECK 5 is handled implicitly by ensuring eval_system parsed explicitly in pdf_parser overrides boilerplate.

    eligible = len(hard_failures) == 0

    return {
        'eligible': eligible,
        'hard_failures': hard_failures,
        'logistics_warnings': logistics_warnings,
        'max_achievable_functionality_pct': max_achievable_functionality_pct,
        'confidence': confidence
    }

if __name__ == "__main__":
    test_text = """
    EVALUATION CRITERIA: Functionality
    The minimum qualifying score of 80% is required to be eligible for preference points.
    Past contract track record: Max points 30
    Must be registered in Ethekwini municipality.
    Compulsory briefing session will be held.
    """
    test_profile = {
        'pit_total_wins': 0,
        'registered_municipality': 'City of Johannesburg',
        'province': 'Gauteng',
        'has_csd': True,
        'has_cidb': True,
        'has_tax_clearance': True
    }
    res = check_hard_eligibility(test_text, test_profile)
    print("Eligibility Test Result:")
    import json
    print(json.dumps(res, indent=2))
