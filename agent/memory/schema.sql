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
