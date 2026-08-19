-- Canonical company memory schema.
--
-- NOTE ON EVOLUTION: this file is executed with `executescript` on every import
-- of company_store, so every statement must be idempotent. `CREATE TABLE IF NOT
-- EXISTS` does NOT add columns to a table that already exists, which means new
-- columns listed here reach existing databases only via the additive
-- ALTER TABLE migration in company_store._migrate_company_profile().
-- Keep the two in step: a column added here must also appear there.

CREATE TABLE IF NOT EXISTS company_profile (
    company_id TEXT PRIMARY KEY,
    company_name TEXT,
    registration_number TEXT,
    csd_number TEXT,
    bbbee_level INTEGER,
    province TEXT,
    registered_municipality TEXT,
    industry TEXT,
    logo_file_path TEXT,

    -- --- Branding, used only to render documents -----------------------------
    -- These two carry no legal weight and go nowhere near a bid form. They
    -- exist so a generated quotation looks like it came from the supplier
    -- rather than from a template: brand_colour drives the whole document
    -- palette (see agent/quotation/quote_theme.py) and tagline is the line
    -- under the wordmark, e.g. "AI SOLUTIONS & VENTURE HOLDINGS".
    --
    -- brand_colour is a #rrggbb string. Anything unparseable falls back to the
    -- olive default rather than failing, because a quotation must always render.
    brand_colour TEXT,
    tagline TEXT,

    -- --- Agent Autofill additions -------------------------------------------
    -- Everything below feeds the DRAFT auto-fill of real South African
    -- government bid forms (SBD 1, SBD 4, SBD 6.1 and friends). A wrong value
    -- stored here propagates into a real submission, so these are only ever
    -- written through a confirmed write path.

    -- JSON array: [{"name": ..., "id_number": ..., "is_state_employee": bool}]
    -- is_state_employee drives the SBD 4 declaration of interest, which is a
    -- sworn declaration. It is never inferred; it is only ever answered.
    directors TEXT,

    postal_address TEXT,
    physical_address TEXT,

    tax_reference_number TEXT,
    vat_registration_number TEXT,

    standard_contact_person TEXT,
    standard_phone TEXT,
    -- Landline and mobile are separate columns because SA bid forms ask for
    -- both. Filling one value into both — which is what a single phone column
    -- forces — puts a visibly wrong answer on the form (MBD 1 asks for
    -- TELEPHONE NUMBER and CELLPHONE NUMBER on adjacent rows).
    standard_cell TEXT,
    standard_fax TEXT,
    standard_email TEXT,
    -- Tax Compliance Status PIN, asked for by name on MBD 1.
    tax_compliance_pin TEXT,
    -- The capacity the signatory signs in ("Director", "Managing Member").
    -- A plain fact, distinct from the signature itself, which is never filled.
    authorized_signatory_capacity TEXT,

    -- LEGAL BOUNDARY -- READ BEFORE USING THIS FIELD.
    --
    -- authorized_signatory_name is a NAME ONLY. It is the plain-text name of
    -- the person authorised to sign on the company's behalf, and it exists so a
    -- draft can print "Name of signatory: ____" pre-filled for a human to
    -- review.
    --
    -- This field MUST NEVER be used to auto-apply a signature image, a scanned
    -- mark, an initial, a drawn squiggle, a font-rendered "signature", or any
    -- other representation of a person having signed. No signature image may
    -- ever be stored in this database, in any column, in any table.
    --
    -- Applying a signature the signatory did not personally apply to that
    -- specific document is forgery, and on a government bid form it is fraud
    -- against the state. Signing stays with the human, on the final document,
    -- every time. This is a legal-exposure boundary, not a style preference.
    authorized_signatory_name TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS company_documents (
    id TEXT PRIMARY KEY,
    company_id TEXT,
    document_type TEXT,
    file_path TEXT,
    expiry_date DATE,
    parsed_fields TEXT, -- JSON string
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES company_profile(company_id)
);

CREATE TABLE IF NOT EXISTS conversation_log (
    id TEXT PRIMARY KEY,
    company_id TEXT,
    user_message TEXT,
    agent_response TEXT,
    tool_calls_made TEXT, -- JSON string
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
