import sys
import os
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from models.pdf_parser import parse_company_pdf
from agent.onboarding.accreditation_advice import get_accreditation_advice
from agent.memory.company_store import add_company_document, update_company_profile

def vet_company_document(company_id: str, file_path: str, doc_type: str = "UNKNOWN"):
    """
    Parses a CIPC or CSD document, stores the parsed info, and returns a non-binding
    draft report on how to maximize win odds.
    """
    path = Path(file_path)
    if not path.exists():
        return {"error": "File not found"}
        
    parsed_fields = parse_company_pdf(path)
    
    # Store document in memory
    add_company_document(company_id, doc_type, file_path, parsed_fields)
    
    # We DO NOT auto-update the company profile here (per prompt: no silent auto-writes).
    # The agent can use this info to ask the user to confirm an update to the profile.
    
    # Generate advice
    advice_list = get_accreditation_advice(parsed_fields)
    
    # Format report
    advice_text = "\n- ".join(advice_list)
    
    report = f"""Based on your uploaded documents, here's what we found and what could improve your competitiveness:
- {advice_text}

[NON-BINDING_NOTICE] This is general guidance based on document analysis, not a compliance audit or legal advice — verify requirements with a procurement professional or the relevant regulatory body before acting."""

    return {
        "parsed_fields": parsed_fields,
        "draft_report": report
    }

onboarding_tools = [
    {
        "name": "vet_company_document",
        "description": "Parses an uploaded CIPC or CSD document and generates a non-binding draft report on how to maximize win odds based on accreditation gaps.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string"
                },
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the uploaded PDF document"
                },
                "doc_type": {
                    "type": "string",
                    "description": "Document type (e.g., CSD_CERT, CIPC_COR14_3)"
                }
            },
            "required": ["company_id", "file_path"]
        }
    }
]
