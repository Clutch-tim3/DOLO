import asyncio
from pathlib import Path
from models.pdf_parser import parse_tender_document
from predict.predict import load_all_artifacts
from app import extract_features_from_tender_id, inject_parsed_features, build_new_features, encode_and_impute, predict
import uuid
import warnings
warnings.filterwarnings('ignore')

artifacts = load_all_artifacts()

async def debug():
    p_lv = parse_tender_document(Path("tests/fixtures/lv_cabling_tender.pdf"))
    p_comms = parse_tender_document(Path("tests/fixtures/rfb_001_comms.docx"))
    
    supplier_name = "WUNDER SA.BI. SRL"
    print(f"\n--- USING REALISTIC INCUMBENT SUPPLIER: {supplier_name} (8 wins) ---")
    
    def get_prob(parsed_tender, supp_name):
        tender_id = str(uuid.uuid4())
        features_df = extract_features_from_tender_id(
            tender_id, supp_name, artifacts["xgb_model"].feature_names, artifacts["medians"]
        )
        features_df = build_new_features(features_df, artifacts["medians"])
        supplier_price = parsed_tender.get('tender_value', 450000) * 0.95
        features_df = inject_parsed_features(features_df, parsed_tender, supplier_price)
        features_df = encode_and_impute(
            features_df, artifacts["encoder"], artifacts["cat_cols"], artifacts["medians"]
        )
        for feat in artifacts["xgb_model"].feature_names:
            if feat not in features_df.columns:
                features_df[feat] = artifacts["medians"].get(feat, 0)
        features_df = features_df[artifacts["xgb_model"].feature_names]
        
        res = predict(artifacts, features_df, mock_supplier_name=supp_name)
        return res["probability"]
        
    prob_lv = get_prob(p_lv, supplier_name)
    prob_comms = get_prob(p_comms, supplier_name)
    
    print(f"LV Cabling Probability (Incumbent 8 wins): {prob_lv*100:.4f}%")
    print(f"RFB Comms Probability (Incumbent 8 wins): {prob_comms*100:.4f}%")
    print(f"Variance: {abs(prob_lv - prob_comms)*100:.4f} points")

asyncio.run(debug())
