import sys
import os
from pathlib import Path

# Ensure models can be imported
sys.path.append(str(Path(__file__).parent.parent.parent))

try:
    from models.sa_scoring import get_bbbee_recommendation
except ImportError:
    # Fallback if pathing fails
    def get_bbbee_recommendation(bbbee_level):
        if bbbee_level in [1, 2]:
            return "Your B-BBEE level is highly competitive. You receive maximum preferential points."
        elif bbbee_level in [3, 4]:
            return "Your B-BBEE level is competitive. Consider improving to Level 2 for maximum points."
        elif bbbee_level in [5, 6]:
            return "Your B-BBEE level is below average. Improving your B-BBEE certificate would significantly increase your preferential points."
        elif bbbee_level in [7, 8]:
            return "Your B-BBEE level is non-competitive. Priority action: improve B-BBEE rating before bidding on high-value tenders."
        else:
            return "No B-BBEE certificate detected. You will receive 0 preferential points. This significantly reduces your competitiveness on SA government tenders."

def get_accreditation_advice(parsed_fields: dict) -> list:
    """
    Maps compliance gaps to specific actions based on parsed document fields.
    """
    advice = []
    
    bbbee_level = parsed_fields.get("bbbee_level")
    advice.append(get_bbbee_recommendation(bbbee_level))
    
    if not parsed_fields.get("supplier_number"):
        advice.append("CSD registration is mandatory for most SA government tenders — register at the Central Supplier Database.")
        
    industry = parsed_fields.get("industry", "").lower()
    if not parsed_fields.get("cidb_grading") and ("construction" in industry or "works" in industry):
        advice.append("Consider CIDB registration if bidding on construction/works tenders — many require a minimum grading.")
        
    return advice
