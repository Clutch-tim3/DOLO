import pytest
from models.sa_scoring import calculate_total_sa_score, calculate_price_score, adjust_probability_for_sa

def test_bbbee_level_1_maximum_points():
    assert calculate_total_sa_score(100, 100, 1, 1000000, evaluation_system_override="80/20")['bbbee_points'] == 20
    assert calculate_total_sa_score(100, 100, 1, 1000000, evaluation_system_override="90/10")['bbbee_points'] == 10

def test_non_compliant_zero_points():
    assert calculate_total_sa_score(100, 100, 0, 1000000, evaluation_system_override="80/20")['bbbee_points'] == 0

def test_price_score_formula_matches_pppfa():
    # PPPFA 80/20: Ps = 80 * (1 - (Pt - Pmin)/Pmin)
    # Pt = 100, Pmin = 100 -> 80 * (1 - 0) = 80
    assert calculate_price_score(100, 100, "80/20") == 80
    
    # Pt = 120, Pmin = 100 -> 80 * (1 - (20/100)) = 80 * 0.8 = 64
    assert calculate_price_score(120, 100, "80/20") == 64
    
    # Pt = 200, Pmin = 100 -> 80 * (1 - 1) = 0
    assert calculate_price_score(200, 100, "80/20") == 0

def test_specific_goals_not_assumed_pure_bbbee():
    res = calculate_total_sa_score(100, 100, 1, 1000000, evaluation_system_override="80/20")
    assert res['bbbee_points'] == 20

def test_probability_bounds_never_exceeded():
    res1 = calculate_total_sa_score(50, 100, 1, 1000000, evaluation_system_override="80/20")
    adj1 = adjust_probability_for_sa(0.9, res1, 1)
    assert adj1['final_probability'] <= 0.95
    
    res2 = calculate_total_sa_score(200, 100, 0, 1000000, evaluation_system_override="80/20")
    adj2 = adjust_probability_for_sa(0.01, res2, 10)
    assert adj2['final_probability'] >= 0.02
