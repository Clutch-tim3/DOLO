import asyncio
import duckdb
from pathlib import Path
import pandas as pd
from models.pdf_parser import parse_tender_document
from predict.predict import load_all_artifacts
from app import inject_parsed_features, build_new_features, encode_and_impute, predict
import warnings
import uuid
import sys
warnings.filterwarnings('ignore')

artifacts = load_all_artifacts()

# Ensure we use new model features exactly
feature_list = artifacts["xgb_model"].feature_names

def run_test_scenario(supplier_name: str, p_lv, p_comms, base_df: pd.DataFrame):
    print(f"\n--- SCENARIO: {supplier_name} ---")
    if base_df is None:
        print("Using cold-start medians.")
        base_df = pd.DataFrame([{feat: artifacts["medians"].get(feat, 0) for feat in feature_list}], columns=feature_list)
    else:
        print(f"Total past wins loaded: {base_df['pit_total_wins'].iloc[0]}")
        
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
    
    print(f"LV Cabling Probability: {prob_lv*100:.4f}%")
    print(f"RFB Comms Probability:  {prob_comms*100:.4f}%")
    variance = abs(prob_lv - prob_comms)
    print(f"Variance: {variance*100:.4f} points")
    return variance

async def debug():
    p_lv = parse_tender_document(Path("tests/fixtures/lv_cabling_tender.pdf"))
    p_comms = parse_tender_document(Path("tests/fixtures/rfb_001_comms.docx"))
    
    # 1. Cold start
    var_cold = run_test_scenario("UNKNOWN COLD START", p_lv, p_comms, None)
    
    # 2. Realistic incumbent (WUNDER SA.BI. SRL has ~8 wins historically)
    con = duckdb.connect()
    query = "SELECT * FROM read_parquet('data/processed/master_training_dataset.parquet') WHERE bidder_name = 'WUNDER SA.BI. SRL' LIMIT 1"
    row_df = con.execute(query).fetchdf()
    if row_df.empty:
        base_df_realistic = None
        print("Warning: WUNDER SA.BI. SRL not found in training dataset.")
    else:
        row = row_df.iloc[0]
        base_df_realistic = pd.DataFrame([{feat: row[feat] if feat in row and pd.notna(row[feat]) else artifacts["medians"].get(feat, 0) for feat in feature_list}], columns=feature_list)
    
    var_realistic = run_test_scenario("WUNDER SA.BI. SRL", p_lv, p_comms, base_df_realistic)
    
    # 3. Large incumbent (find someone with 50-150 wins in the new cleaned dataset)
    query_large = "SELECT bidder_name FROM read_parquet('data/processed/master_training_dataset.parquet') WHERE pit_total_wins BETWEEN 50 AND 150 LIMIT 1"
    large_name_df = con.execute(query_large).fetchdf()
    if not large_name_df.empty:
        large_name = large_name_df.iloc[0]['bidder_name']
        query_large_data = f"SELECT * FROM read_parquet('data/processed/master_training_dataset.parquet') WHERE bidder_name = '{large_name}' LIMIT 1"
        row_df_large = con.execute(query_large_data).fetchdf()
        row_large = row_df_large.iloc[0]
        base_df_large = pd.DataFrame([{feat: row_large[feat] if feat in row_large and pd.notna(row_large[feat]) else artifacts["medians"].get(feat, 0) for feat in feature_list}], columns=feature_list)
        var_large = run_test_scenario(large_name, p_lv, p_comms, base_df_large)
    else:
        print("Warning: No supplier with 50-150 wins found.")
        var_large = 0

asyncio.run(debug())
