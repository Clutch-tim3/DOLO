import pandas as pd
import json
import os
from pathlib import Path
from predict.predict import load_all_artifacts, extract_features_from_file, encode_and_impute

print("--- 1. ROW COUNT BREAKDOWN ---")
try:
    with open('data/processed/dataset_metadata.json') as f:
        meta = json.load(f)
    print("Metadata rows:", meta.get('total_rows'))
    print("Metadata GPPD vs SA rows:", meta.get('source_breakdown', 'Not found in metadata'))
except Exception as e:
    print("Metadata Error:", e)

# We can query the parquet directly
import duckdb
print("\n--- QUERY PARQUET ---")
try:
    df = duckdb.query("SELECT source_country, COUNT(*) as count FROM 'data/processed/raw_contracts.parquet' GROUP BY source_country").df()
    print("raw_contracts.parquet:")
    print(df.to_string(index=False))
except Exception as e:
    print("Query error:", e)

try:
    df_train = duckdb.query("SELECT source_country, COUNT(*) as count FROM 'data/processed/master_training_dataset.parquet' GROUP BY source_country").df()
    print("\nmaster_training_dataset.parquet:")
    print(df_train.to_string(index=False))
except Exception as e:
    print("Query error:", e)


print("\n--- 2. RAW FEATURE VECTORS ---")
try:
    artifacts = load_all_artifacts()
    feature_list = artifacts['metadata']['feature_columns']
    medians = artifacts['medians']
    encoder = artifacts['encoder']
    
    # We find cat cols from the pipeline if possible, or just look at extract_features_from_file
    files = ["lv_cabling_tender.pdf", "rfb_001_comms.docx"]
    supplier = "TEST SUPPLIER"
    
    for f in files:
        path = os.path.join("tests", "fixtures", f)
        print(f"\nEvaluating: {f}")
        df = extract_features_from_file(path, supplier, feature_list, medians)
        
        cols = ['tender_estimatedpriceUsd', 'deadline_days', 'tender_year']
        present_cols = [c for c in cols if c in df.columns]
        print("PRE-ENCODING (Raw parsed features):")
        print(df[present_cols].to_string())
        
        # Look for cat cols in encoder
        cat_cols = getattr(encoder, 'feature_names_in_', [])
        if not len(cat_cols):
            cat_cols = ['buyer_country', 'buyer_buyertype', 'tender_proceduretype', 'tender_supplytype']
            
        encoded_df = encode_and_impute(df.copy(), encoder, cat_cols, medians)
        print("POST-ENCODING (Immediate input to predict_proba):")
        print(encoded_df[present_cols].to_string())
        
except Exception as e:
    import traceback
    traceback.print_exc()
