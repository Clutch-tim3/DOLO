import asyncio
import duckdb
from pathlib import Path
import pandas as pd
from models.pdf_parser import parse_tender_document
from predict.predict import load_all_artifacts
from app import inject_parsed_features, build_new_features, encode_and_impute, predict
import warnings
warnings.filterwarnings('ignore')

artifacts = load_all_artifacts()

async def debug():
    p_lv = parse_tender_document(Path("tests/fixtures/lv_cabling_tender.pdf"))
    p_comms = parse_tender_document(Path("tests/fixtures/rfb_001_comms.docx"))
    
    supplier_name = "WUNDER SA.BI. SRL"
    print(f"\n--- USING REALISTIC INCUMBENT SUPPLIER: {supplier_name} ---")
    
    # 1. Fetch supplier features via DuckDB
    con = duckdb.connect()
    # Fetch exactly one row for the supplier
    query = f"SELECT * FROM read_parquet('data/processed/master_training_dataset.parquet') WHERE bidder_name = '{supplier_name}' LIMIT 1"
    row_df = con.execute(query).fetchdf()
    
    if row_df.empty:
        print("Supplier not found!")
        return
        
    print(f"Supplier found! Total past wins: {row_df['pit_total_wins'].iloc[0]}")
    
    # Extract just the features we need
    feature_list = artifacts["xgb_model"].feature_names
    row = row_df.iloc[0]
    base_df = pd.DataFrame([{feat: row[feat] if feat in row and pd.notna(row[feat]) else artifacts["medians"].get(feat, 0) for feat in feature_list}], columns=feature_list)
    
    def get_prob(parsed_tender, supp_name):
        features_df = base_df.copy()
        features_df = build_new_features(features_df, artifacts["medians"])
        supplier_price = parsed_tender.get('tender_value', 450000) * 0.95
        features_df = inject_parsed_features(features_df, parsed_tender, supplier_price)
        features_df = encode_and_impute(
            features_df, artifacts["encoder"], artifacts["cat_cols"], artifacts["medians"]
        )
        for feat in feature_list:
            if feat not in features_df.columns:
                features_df[feat] = artifacts["medians"].get(feat, 0)
        features_df = features_df[feature_list]
        
        res = predict(artifacts, features_df, mock_supplier_name=supp_name)
        return res["probability"]
        
    prob_lv = get_prob(p_lv, supplier_name)
    prob_comms = get_prob(p_comms, supplier_name)
    
    print(f"LV Cabling Probability (Incumbent): {prob_lv*100:.4f}%")
    print(f"RFB Comms Probability (Incumbent): {prob_comms*100:.4f}%")
    print(f"Variance: {abs(prob_lv - prob_comms)*100:.4f} points")

asyncio.run(debug())
