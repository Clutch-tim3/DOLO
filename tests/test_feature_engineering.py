import pytest
import pandas as pd
from predict.predict import extract_features_from_tender_id, build_new_features, encode_and_impute
from app import inject_parsed_features
from models.pdf_parser import parse_tender_document
import json

def test_feature_vector_differs_across_different_tenders(fixtures_dir, loaded_models):
    artifacts = loaded_models
    feature_list = artifacts["dataset_metadata"]["features"]
    
    docs = ["alfred_duma.pdf", "lv_cabling_tender.pdf", "rfb_001_comms.docx"]
    vectors = []
    
    for i, doc in enumerate(docs):
        parsed = parse_tender_document(fixtures_dir / doc)
        df = extract_features_from_tender_id(f"t{i+1}", "TEST SUPPLIER", feature_list, artifacts["medians"])
        df = build_new_features(df, artifacts["medians"])
        df = inject_parsed_features(df, parsed)
        df = encode_and_impute(df, artifacts["encoder"], artifacts["cat_cols"], artifacts["medians"])
        vectors.append(df.iloc[0])
        
    v1, v2, v3 = vectors
    
    # Assert at least 8 features differ between v1 and v2
    diff_1_2 = sum(1 for c in v1.index if v1[c] != v2[c])
    assert diff_1_2 >= 8
    
def test_no_feature_vector_is_all_defaults(fixtures_dir, loaded_models):
    artifacts = loaded_models
    feature_list = artifacts["dataset_metadata"]["features"]
    docs = ["alfred_duma.pdf", "lv_cabling_tender.pdf", "rfb_001_comms.docx"]
    
    for doc in docs:
        parsed = parse_tender_document(fixtures_dir / doc)
        df = extract_features_from_tender_id("t1", "TEST SUPPLIER", feature_list, artifacts["medians"])
        df = build_new_features(df, artifacts["medians"])
        df = inject_parsed_features(df, parsed)
        df = encode_and_impute(df, artifacts["encoder"], artifacts["cat_cols"], artifacts["medians"])
        
        # Check that we have actual values (not just medians)
        assert df['deadline_days'].iloc[0] != 0

def test_point_in_time_supplier_lookup_matches_correctly(loaded_models):
    artifacts = loaded_models
    feature_list = artifacts["dataset_metadata"]["features"]
    
    df1 = extract_features_from_tender_id("t1", "ACME (PTY) LTD", feature_list, artifacts["medians"])
    df2 = extract_features_from_tender_id("t1", "ACME PTY LTD", feature_list, artifacts["medians"])
    
    assert df1['pit_total_wins'].iloc[0] == df2['pit_total_wins'].iloc[0]

def test_categorical_encoding_not_stale(loaded_models):
    artifacts = loaded_models
    feature_list = artifacts["dataset_metadata"]["features"]
    
    df1 = extract_features_from_tender_id("t1", "SUPP1", feature_list, artifacts["medians"])
    df1['tender_proceduretype'] = "Open"
    df1 = encode_and_impute(df1, artifacts["encoder"], artifacts["cat_cols"], artifacts["medians"])
    
    df2 = extract_features_from_tender_id("t1", "SUPP1", feature_list, artifacts["medians"])
    df2['tender_proceduretype'] = "RFP"
    df2 = encode_and_impute(df2, artifacts["encoder"], artifacts["cat_cols"], artifacts["medians"])
    
    assert df1['tender_proceduretype'].iloc[0] != df2['tender_proceduretype'].iloc[0]

def test_no_leakage_correlation():
    df = pd.read_parquet("data/processed/master_training_dataset.parquet")
    val_df = df[df['split'] == 'val']
    corrs = val_df.corr(numeric_only=True)['did_win'].abs()
    
    # Exclude the label itself
    corrs = corrs.drop('did_win', errors='ignore')
    
    # Assert no feature exceeds 0.5 absolute correlation (no extreme leakage)
    assert (corrs > 0.5).sum() == 0
