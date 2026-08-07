memory_tools = [
    {
        "name": "get_company_profile",
        "description": "Reads the canonical company profile from the database. Call this at the start of every session before answering anything company-specific.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "The ID of the company"
                }
            },
            "required": ["company_id"]
        }
    },
    {
        "name": "get_company_documents",
        "description": "Reads the canonical company documents from the database. Call this at the start of every session before answering anything company-specific.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "The ID of the company"
                }
            },
            "required": ["company_id"]
        }
    },
    {
        "name": "update_company_profile",
        "description": (
            "Writes updates to the company profile database. These values auto-fill real "
            "South African government tender documents, so a wrong value here ends up in a "
            "real submission.\n\n"
            "TWO-STEP WRITE. Call this first WITHOUT confirmed (or with confirmed=false). "
            "Nothing is written; you get back pending_changes, a literal before/after list. "
            "Show that list to the user verbatim and ask them to confirm. Only after the "
            "user has actually answered may you call again with confirmed=true.\n\n"
            "Never set confirmed=true on the user's behalf, and never infer a value from "
            "conversation or from a parsed document without the user confirming it first. "
            "The refusal is enforced in code, so guessing wastes a turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "The ID of the company"
                },
                "fields": {
                    "type": "object",
                    "description": (
                        "Key-value pairs of fields to update. Valid keys: company_name, "
                        "registration_number, csd_number, bbbee_level, province, "
                        "registered_municipality, industry, logo_file_path, directors, "
                        "postal_address, physical_address, tax_reference_number, "
                        "vat_registration_number, standard_contact_person, standard_phone, "
                        "standard_email, authorized_signatory_name.\n"
                        "directors is an array of {name, id_number, is_state_employee}; "
                        "is_state_employee is a sworn SBD 4 declaration and must be answered "
                        "by the user, never assumed.\n"
                        "authorized_signatory_name is a NAME ONLY. It is never a signature, "
                        "an image, or a file path, and CairoAI never signs anything."
                    )
                },
                "confirmed": {
                    "type": "boolean",
                    "description": (
                        "Set true ONLY after the user has been shown the exact pending_changes "
                        "from a previous unconfirmed call and has explicitly approved them. "
                        "Defaults to false, which refuses the write."
                    )
                }
            },
            "required": ["company_id", "fields"]
        }
    },
    {
        "name": "search_conversation_history",
        "description": "Reads recent conversation log entries for context continuity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "The ID of the company"
                },
                "query": {
                    "type": "string",
                    "description": "Optional search term to filter history"
                }
            },
            "required": ["company_id"]
        }
    }
]
