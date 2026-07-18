import pytest
from app import inject_parsed_features
from predict.predict import extract_features_from_tender_id, build_new_features, encode_and_impute, predict
from models.pdf_parser import parse_tender_document

def _get_prob(fixtures_dir, loaded_models, profile):
    artifacts = loaded_models
    parsed = parse_tender_document(fixtures_dir / "lv_cabling_tender.pdf")
    feature_list = artifacts["dataset_metadata"]["features"]
    
    df = extract_features_from_tender_id("t1", profile['company_name'], feature_list, artifacts["medians"])
    
    # Inject profile stats
    df["pit_total_wins"] = profile["pit_total_wins"]
    df["pit_win_rate_overall"] = profile["pit_win_rate_overall"]
    df["pit_is_incumbent"] = profile.get("pit_is_incumbent", 0)
    df["pit_buyer_win_count"] = profile.get("pit_buyer_win_count", 0)
    
    df = build_new_features(df, artifacts["medians"])
    df = inject_parsed_features(df, parsed)
    df = encode_and_impute(df, artifacts["encoder"], artifacts["cat_cols"], artifacts["medians"])
    
    # We pass empty parsed tender to predict just to get the pipeline
    raw_prob, _ = predict("t1", profile['company_name'], parsed, artifacts, injected_df=df)
    return raw_prob

def test_new_bidder_baseline_probability_range(fixtures_dir, loaded_models, sample_supplier_profile_new_bidder):
    prob = _get_prob(fixtures_dir, loaded_models, sample_supplier_profile_new_bidder)
    assert 0.10 <= prob <= 0.25

def test_experienced_non_incumbent_probability_range(fixtures_dir, loaded_models, sample_supplier_profile_experienced):
    prob = _get_prob(fixtures_dir, loaded_models, sample_supplier_profile_experienced)
    assert 0.25 <= prob <= 0.40

def test_incumbent_probability_range(fixtures_dir, loaded_models, sample_supplier_profile_incumbent):
    prob = _get_prob(fixtures_dir, loaded_models, sample_supplier_profile_incumbent)
    assert 0.50 <= prob <= 0.70

def test_probability_ladder_ordering(fixtures_dir, loaded_models, sample_supplier_profile_new_bidder, sample_supplier_profile_experienced, sample_supplier_profile_incumbent):
    p_new = _get_prob(fixtures_dir, loaded_models, sample_supplier_profile_new_bidder)
    p_exp = _get_prob(fixtures_dir, loaded_models, sample_supplier_profile_experienced)
    p_inc = _get_prob(fixtures_dir, loaded_models, sample_supplier_profile_incumbent)
    
    assert p_new < p_exp < p_inc

def test_alfred_duma_disqualification_reasons_unchanged(alfred_duma_text, sample_supplier_profile_new_bidder):
    from predict.eligibility_gate import check_hard_eligibility
    res = check_hard_eligibility(alfred_duma_text, sample_supplier_profile_new_bidder)
    assert res['eligible'] is False
    reasons = [f['reason'] for f in res['hard_failures']]
    assert len(reasons) == 2
    assert any("Functionality gate requires" in r for r in reasons)
    assert any("Tender requires location" in r for r in reasons)
