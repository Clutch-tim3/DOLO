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
        "description": "Writes updates to the company profile database. REQUIRES explicit user confirmation before any write. Never auto-write based on inferred conversation content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "The ID of the company"
                },
                "fields": {
                    "type": "object",
                    "description": "Key-value pairs of fields to update. Valid keys: company_name, registration_number, csd_number, bbbee_level, province, registered_municipality, industry, logo_file_path"
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
