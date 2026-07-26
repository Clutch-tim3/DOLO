import sys
import os
from pathlib import Path
sys.path.append(os.getcwd())

from agent.memory.company_store import update_company_profile, get_company_profile, get_company_documents
from agent.onboarding.vet_company import vet_company_document
from agent.quotation.extract_line_items import extract_line_items
from agent.quotation.price_search import get_prices_for_items
from agent.main_agent import generate_draft_quote_flow, finalize_quote_flow

def run_integration_test():
    print("--- 1. SIGNUP & ONBOARDING VETTING ---")
    company_id = "test_co_123"
    
    # Simulate saving initial profile fields
    update_company_profile(company_id, {"company_name": "Acme Corp"})
    
    # Mocking a CSD document (use an existing one if possible, or just mock it)
    # Since we need a real document for parse_company_pdf to not error, I'll write a dummy PDF text later, 
    # but for now let's see what happens with a missing file. Wait, vet_company checks if it exists.
    # Let's create a dummy text file and pretend it's a PDF.
    Path("dummy_csd.txt").write_text("Enterprise Name: Acme Corp\nMAAA1234567\nB-BBEE Level: Level 2")
    vet_res = vet_company_document(company_id, "dummy_csd.txt", "CSD_CERT")
    print("Vetting Report:\n", vet_res.get("draft_report"))
    
    print("\n--- 2. MEMORY PERSISTS ---")
    prof = get_company_profile(company_id)
    docs = get_company_documents(company_id)
    print(f"Loaded Profile: {prof.get('company_name')}")
    print(f"Loaded Docs Count: {len(docs)}")
    
    print("\n--- 3. USER ASKS TO DRAFT QUOTE ---")
    # Mock tender PDF text
    Path("alfred_duma.txt").write_text("Alfred Duma stationery tender for paper and pens and files")
    
    draft_res = generate_draft_quote_flow(company_id, "alfred_duma.txt")
    quote_id = draft_res["quote_id"]
    print("Draft Quote:\n", draft_res["draft_document"])
    
    print("\n--- 4. ATTEMPT FINALIZATION WITH UNRESOLVED FLAGS ---")
    # Grab the priced items (which we would normally pull from DB or state)
    items = extract_line_items("alfred_duma.txt")
    priced_items = get_prices_for_items(items)
    
    final_res = finalize_quote_flow(quote_id, priced_items)
    print("Finalization attempt 1 result:", final_res)
    
    print("\n--- 5. USER CONFIRMS FLAGGED ITEMS ---")
    # Simulate user manually setting prices for the missing/flagged ones
    for item in priced_items:
        if item.get("price_status") in ["MANUAL_REVIEW_REQUIRED", "LOW_CONFIDENCE"]:
            item["price"] = 150.0  # User manually inputs
            item["total"] = item["price"] * item["quantity"]
            item["price_status"] = "USER_CONFIRMED"
            item["retailer_name"] = "Manual Override"
            
    final_res2 = finalize_quote_flow(quote_id, priced_items)
    print("Finalization attempt 2 result:", final_res2)

if __name__ == "__main__":
    run_integration_test()
