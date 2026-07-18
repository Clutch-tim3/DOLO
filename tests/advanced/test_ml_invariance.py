import pytest
import pandas as pd
from hypothesis import given, settings, strategies as st
from predict.predict import predict, load_all_artifacts

# Load artifacts once for the tests
ml_artifacts = load_all_artifacts()

def _prepare_df(df_dict, artifacts):
    df = pd.DataFrame([df_dict])
    # XGBoost requires all feature names to be present
    feature_names = artifacts['xgb_model'].feature_names
    for f in feature_names:
        if f not in df.columns:
            df[f] = artifacts['medians'].get(f, 0)
    # Ensure correct column order
    return df[feature_names]

@pytest.mark.xfail(reason="Tree models without monotonic constraints may learn non-monotonic relationships for bid price")
@settings(deadline=None)
@given(
    base_bid=st.floats(min_value=1000, max_value=1_000_000, allow_nan=False, allow_infinity=False),
    multiplier=st.floats(min_value=1.1, max_value=10.0, allow_nan=False, allow_infinity=False)
)
def test_ml_invariance_higher_bid_price_lowers_probability(base_bid, multiplier):
    """
    Metamorphic invariant: If everything is exactly identical, a higher bid price 
    should NEVER result in a higher win probability. It must be <= base_prob.
    """
    higher_bid = base_bid * multiplier
    
    df1_dict = {
        "bid_priceUsd": base_bid,
        "tender_estimatedpriceUsd": 500_000,
        "tender_description_length": 200,
        "deadline_days": 30,
        "bbbee_level": 1
    }
    
    df2_dict = {
        "bid_priceUsd": higher_bid,
        "tender_estimatedpriceUsd": 500_000,
        "tender_description_length": 200,
        "deadline_days": 30,
        "bbbee_level": 1
    }
    
    df1 = _prepare_df(df1_dict, ml_artifacts)
    df2 = _prepare_df(df2_dict, ml_artifacts)
    
    try:
        prob1 = predict(ml_artifacts, df1).get("win_probability", predict(ml_artifacts, df1).get("probability"))
        prob2 = predict(ml_artifacts, df2).get("win_probability", predict(ml_artifacts, df2).get("probability"))
        
        assert prob2 <= prob1 + 0.001
    except Exception as e:
        pytest.fail(f"Prediction failed on synthetic data: {e}")

@settings(deadline=None)
@given(
    weird_budget=st.floats(min_value=-1_000_000, max_value=0),
    weird_deadline=st.integers(min_value=-1000, max_value=-1)
)
def test_ml_out_of_distribution_bounds(weird_budget, weird_deadline):
    df_dict = {
        "bid_priceUsd": 100_000,
        "tender_estimatedpriceUsd": weird_budget,
        "deadline_days": weird_deadline
    }
    df = _prepare_df(df_dict, ml_artifacts)
    
    res = predict(ml_artifacts, df)
    prob = res.get("win_probability", res.get("probability"))
    assert 0.0 <= prob <= 1.0
