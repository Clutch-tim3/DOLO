APP_KNOWLEDGE_BASE = {
    "Sort Page": "The Sort Page (or Sort & Rank Tenders page) is where users upload batch tender documents (PDF/DOCX) to be scored by the ML predictive pipeline. It displays the AI win-probability predictions and separates tenders into Recommended and Not Recommended.",
    "Archive": "The Archive page stores past tenders that have been processed. Users can review historical predictions and access old tender documents here.",
    "Calendar": "The Tender Calendar surfaces and organizes tender dates (briefing sessions, closing dates, award dates). It highlights overlapping or conflicting dates for multiple tenders.",
    "Vault": "The Vault is a secure document storage area where users upload their company compliance documents (CIPC, CSD, Tax Clearance, B-BBEE certificates, CIDB grading). These documents are parsed during onboarding to provide vetting advice and auto-fill quotations.",
    "System Page": "The System Status (or Model Transparency) page allows users to view the underlying model performance metrics, switch between different evaluation models (e.g., CatBoost vs LightGBM), and see exactly how the AI weights different features."
}

def get_app_help(feature_query: str) -> str:
    """
    Returns static help documentation about app features.
    Matches the query against the knowledge base keys.
    """
    feature_query = feature_query.lower()
    matches = []
    
    # Simple keyword matching
    keywords = feature_query.split()
    
    for key, description in APP_KNOWLEDGE_BASE.items():
        if key.lower() in feature_query or feature_query in key.lower() or any(kw in key.lower() for kw in keywords if len(kw) > 3):
            matches.append(f"**{key}**: {description}")
            
    if matches:
        return "\n\n".join(matches)
    
    # Fallback
    available = ", ".join(APP_KNOWLEDGE_BASE.keys())
    return f"I couldn't find specific help for that query. The available features I can explain are: {available}."

app_help_tools = [
    {
        "name": "get_app_help",
        "description": "Retrieves factual information about the CairoAI app's features and UI to answer 'how do I...' questions. Prevents describing features that do not exist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "feature_query": {
                    "type": "string",
                    "description": "The feature or page the user is asking about (e.g., 'Sort Page', 'Calendar', 'Vault')"
                }
            },
            "required": ["feature_query"]
        }
    }
]
