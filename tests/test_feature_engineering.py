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

    # What this is really asking: does reading a document put that document's
    # own numbers into the feature vector, or does every tender come out the
    # same?
    #
    # It used to assert ">= 8 of 67 columns differ", which is a count with no
    # meaning behind it. 60 of those 67 are supplier- and buyer-history
    # features (pit_*, buyer_*), and both rows here are the SAME supplier with
    # no history, so they are identical by construction — no parser change
    # could ever move them. Only the tender-level columns can differ, and the
    # arbitrary bar sat one above how many there are.
    #
    # So name them instead. This fails if the parser stops extracting any one
    # of them, which the count could not distinguish from a fixture changing.
    from_the_document = [
        "deadline_days",
        "tender_description_length",
        "bid_priceUsd",
        "tender_estimatedpriceUsd",
        "had_functionality_gate",
        "tender_supplytype",
    ]
    for column in from_the_document:
        assert v1[column] != v2[column], (
            f"{column} is identical across two different tenders — "
            f"the parser is not extracting it"
        )

    # tender_supplytype above is the one that had to be *fixed* to get here.
    # The OrdinalEncoder had been fitted on a one-row training split, so it
    # knew a single category and every document encoded to -1: two different
    # tenders, same dead value. It is in the list to keep that closed.
    assert v1["tender_supplytype"] >= 0 and v2["tender_supplytype"] >= 0, (
        "supply type encoded as unknown (-1) — the encoder's vocabulary is "
        "missing categories that exist in the training data"
    )
    
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
