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
                    # The model only knows what this string tells it. Six fields
                    # the store accepts were missing from it — standard_cell,
                    # standard_fax, tax_compliance_pin,
                    # authorized_signatory_capacity, brand_colour and tagline —
                    # so asking the agent to set a cell number or a tax
                    # compliance PIN got a refusal for a field that works. The
                    # first three are why a filled MBD 1 still showed blanks.
                    #
                    # tests/test_profile_schema_matches_store.py fails if this
                    # list and PROFILE_WRITABLE_FIELDS drift apart again.
                    "description": (
                        "Key-value pairs of fields to update. Valid keys: company_name, "
                        "registration_number, csd_number, bbbee_level, province, "
                        "registered_municipality, industry, logo_file_path, directors, "
                        "postal_address, physical_address, tax_reference_number, "
                        "vat_registration_number, standard_contact_person, standard_phone, "
                        "standard_cell, standard_fax, standard_email, "
                        "authorized_signatory_name, authorized_signatory_capacity, "
                        "tax_compliance_pin, brand_colour, tagline.\n"
                        "directors is an array of {name, id_number, is_state_employee}; "
                        "is_state_employee is a sworn SBD 4 declaration and must be answered "
                        "by the user, never assumed.\n"
                        "authorized_signatory_name is a NAME ONLY. It is never a signature, "
                        "an image, or a file path, and CairoAI never signs anything.\n"
                        "authorized_signatory_capacity is the role printed under the "
                        "signature line, e.g. 'Director' or 'Managing Member'.\n"
                        "standard_cell and standard_fax are asked for on their own rows on "
                        "MBD 1; a missing cell number leaves a blank on a submitted form.\n"
                        "tax_compliance_pin is the SARS Tax Compliance Status PIN, asked for "
                        "by name on MBD 1. It is a reference the buyer uses to verify status "
                        "— never invent or guess one.\n"
                        "brand_colour is a hex colour such as '#1A4D8F' and drives the "
                        "quotation palette; tagline is the line printed under the wordmark.\n"
                        "owned_51pc_black, owned_51pc_black_women, owned_51pc_black_youth "
                        "and owned_51pc_black_disability are the SBD 6.1 specific-goals "
                        "flags. Each is true ONLY if the company is at least 51% owned by "
                        "that group. NEVER infer one: take it from the B-BBEE certificate "
                        "or EME affidavit, or from the user directly. These decide "
                        "preference points on a real bid, so claiming one the company does "
                        "not qualify for is a false claim on a government tender. A "
                        "certificate reading '0% BLACK FEMALE OWNERSHIP' means that flag is "
                        "FALSE, not unknown."
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
