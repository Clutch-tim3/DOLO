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
        "name": "get_vault_status",
        "description": (
            "Checks which of the five required Compliance Vault documents this company has "
            "uploaded (Tax Clearance, B-BBEE Certificate, CIDB Grading, CSD Report, CIPC "
            "Registration) and returns the facts already parsed out of them, including the "
            "company's industry and CSD commodity categories. Call this before offering to vet "
            "the company, and use parsed_company_facts to work out what the company actually "
            "does. Takes no arguments beyond company_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"company_id": {"type": "string"}},
            "required": ["company_id"],
        },
    },
    {
        "name": "generate_accreditation_report",
        "description": (
            "Renders a downloadable PDF roadmap of the accreditations, licences and "
            "certifications this specific company should pursue. YOU decide what to recommend, "
            "based on the company's industry and CSD commodity categories from get_vault_status "
            "— this tool only lays out what you supply. Recommend things that genuinely fit "
            "their line of work and that improve competitiveness on South African government "
            "tenders. Order matters less than accuracy: do not pad the list. Returns a pdf_url "
            "to give the user. The PDF already carries a non-binding disclaimer stating the "
            "links are AI-generated, so you do not need to repeat that at length in chat."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "items": {
                    "type": "array",
                    "description": "The accreditations to include, most important first.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "What to obtain, e.g. 'ISO 9001 Quality Management certification'.",
                            },
                            "body": {
                                "type": "string",
                                "description": "The organisation that issues or regulates it.",
                            },
                            "category": {
                                "type": "string",
                                "description": "Accreditation, Licence or Certification.",
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                                "description": "high = likely blocking them from tenders they could otherwise win.",
                            },
                            "why": {
                                "type": "string",
                                "description": "Why it matters for THIS company, referencing their actual industry.",
                            },
                            "url": {
                                "type": "string",
                                "description": "Direct https link to the issuing body or application page.",
                            },
                        },
                        "required": ["name", "priority", "why"],
                    },
                },
            },
            "required": ["company_id", "items"],
        },
    },
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
