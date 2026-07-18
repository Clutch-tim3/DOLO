import sys
import pandas as pd
from pathlib import Path

sys.path.append("/Users/harry/Documents/Data set V2/tender_ml")
from app import extract_features_from_tender_id, inject_parsed_features, build_new_features, encode_and_impute, parse_tender_document
from predict.predict import predict, load_all_artifacts

artifacts = load_all_artifacts()
medians = artifacts["medians"]
feature_list = artifacts["xgb_model"].feature_names
cat_cols = artifacts["cat_cols"]
encoder = artifacts["encoder"]

for file in ["tests/fixtures/DF 04 2026.pdf", "tests/fixtures/lv_cabling_tender.pdf", "tests/fixtures/rfb_001_comms.docx"]:
    if not Path(file).exists() and "DF" in file: file = "tests/fixtures/alfred_duma.pdf"
    
    parsed = parse_tender_document(file)
    print(f"\n--- {file} ---")
    
    features_df = extract_features_from_tender_id("dummy_id", "UNKNOWN", feature_list, medians)
    features_df = inject_parsed_features(features_df, parsed)
    features_df = build_new_features(features_df, medians)
    features_df = encode_and_impute(features_df, encoder, cat_cols, medians)
    
    print("Injected features:", features_df[['tender_estimatedpriceUsd', 'deadline_days', 'tender_description_length']].to_dict('records'))
    
    p = predict(artifacts, features_df, "UNKNOWN")
    print("Prediction Dict:", p)

