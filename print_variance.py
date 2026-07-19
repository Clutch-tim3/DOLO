from app import inject_parsed_features
from predict.predict import load_all_artifacts, extract_features_from_tender_id, build_new_features, encode_and_impute
from models.pdf_parser import parse_tender_document
import os

artifacts = load_all_artifacts()
files = ["lv_cabling_tender.pdf", "rfb_001_comms.docx"]
supplier_name = "TEST SUPPLIER"

for f in files:
    path = os.path.join("tests", "fixtures", f)
    parsed = parse_tender_document(path)
    
    # Simulating what app.py does:
    features_df = extract_features_from_tender_id("dummy", supplier_name, artifacts["metadata"]["feature_columns"], artifacts["medians"])
    features_df = build_new_features(features_df, artifacts["medians"])
    
    # Default supplier price logic in app.py if not provided
    supplier_price = parsed.get("bid_price") if parsed.get("bid_price") else 450000.0
    
    # Inject!
    features_df = inject_parsed_features(features_df, parsed, supplier_price)
    
    # Encode and impute
    cat_cols = artifacts["metadata"].get("categorical_features", ['buyer_country', 'buyer_buyertype', 'tender_proceduretype', 'tender_supplytype'])
    features_df = encode_and_impute(features_df, artifacts["encoder"], cat_cols, artifacts["medians"])
    
    print(f"\n--- RAW FEATURES IMMEDIATELY BEFORE PREDICT_PROBA: {f} ---")
    cols_to_show = ['deadline_days', 'tender_estimatedpriceUsd']
    present = [c for c in cols_to_show if c in features_df.columns]
    print(features_df[present].to_string())

