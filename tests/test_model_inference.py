import pytest
import json
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock
from predict.predict import predict, extract_features_from_tender_id, build_new_features, encode_and_impute
from app import inject_parsed_features
from models.pdf_parser import parse_tender_document

def test_ensemble_receives_distinct_vectors(fixtures_dir, loaded_models):
    artifacts = loaded_models
    docs = ["alfred_duma.pdf", "lv_cabling_tender.pdf", "rfb_001_comms.docx"]
    
    args_passed = []
    def dmatrix_mock(data, *args, **kwargs):
        args_passed.append(data)
        return MagicMock()
    
    with patch("xgboost.DMatrix", side_effect=dmatrix_mock), \
         patch.object(artifacts["xgb_model"], 'predict', return_value=np.array([0.5])) as mock_xgb, \
         patch.object(artifacts["lgb_model"], 'predict', return_value=np.array([0.5])) as mock_lgb, \
         patch.object(artifacts["cb_model"], 'predict_proba', return_value=np.array([[0.5, 0.5]])) as mock_cb:
        
        for doc in docs:
            parsed = parse_tender_document(fixtures_dir / doc)
            
            # Replicate feature extraction & engineering
            feature_list = list(artifacts["metadata"]["features"].keys())
            features_df = extract_features_from_tender_id("t1", "SUPP1", feature_list, artifacts["medians"])
            features_df = build_new_features(features_df, artifacts["medians"])
            features_df = inject_parsed_features(features_df, parsed, 450000.0)
            features_df = encode_and_impute(features_df, artifacts["encoder"], artifacts["cat_cols"], artifacts["medians"])
            
            if artifacts["xgb_model"].feature_names is not None:
                for feat in artifacts["xgb_model"].feature_names:
                    if feat not in features_df.columns:
                        features_df[feat] = artifacts["medians"].get(feat, 0)
                features_df = features_df[artifacts["xgb_model"].feature_names]
                
            predict(artifacts, features_df, mock_supplier_name="SUPP1")
            
        # Assert none are exactly equal
        assert not np.array_equal(args_passed[0].iloc[0].values, args_passed[1].iloc[0].values)
        assert not np.array_equal(args_passed[1].iloc[0].values, args_passed[2].iloc[0].values)
        assert not np.array_equal(args_passed[0].iloc[0].values, args_passed[2].iloc[0].values)

def test_ensemble_weights_sum_to_one():
    with open("models/saved/ensemble_weights.json", "r") as f:
        weights = json.load(f)
    assert abs(sum(weights.values()) - 1.0) < 1e-6

def test_calibrator_applied_consistently(loaded_models):
    artifacts = loaded_models
    raw_prob = 0.4
    calibrated_prob = artifacts["calibrator"].transform([[raw_prob]])[0]
    
    assert calibrated_prob >= 0.0 and calibrated_prob <= 1.0
    
    with patch.object(artifacts["calibrator"], 'transform', return_value=np.array([calibrated_prob])) as mock_cal:
        features_df = pd.DataFrame([{feat: 0.0 for feat in artifacts["xgb_model"].feature_names}], columns=artifacts["xgb_model"].feature_names)
        predict(artifacts, features_df, "SUPP1")
        mock_cal.assert_called()

def test_threshold_loaded_fresh_not_cached_stale(loaded_models, tmp_path):
    # Read original threshold config to restore later
    with open("models/threshold.json", "r") as f:
        orig_threshold_content = f.read()
        
    with open("models/threshold.json", "w") as f:
        json.dump({"threshold_value": 0.99}, f)
        
    try:
        from predict.predict import load_all_artifacts as load_model_artifacts
        new_artifacts = load_model_artifacts()
        assert new_artifacts["threshold"] == 0.99
    finally:
        # Revert
        with open("models/threshold.json", "w") as f:
            f.write(orig_threshold_content)
