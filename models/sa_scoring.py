import math

def get_evaluation_system(tender_value_zar):
    """Returns the evaluation system based on tender value."""
    if tender_value_zar is None:
        return '80/20'
    return '80/20' if tender_value_zar < 50_000_000 else '90/10'

def get_bbbee_points(bbbee_level, evaluation_system):
    """Returns the B-BBEE points for a given level and evaluation system."""
    if bbbee_level is None or bbbee_level == 0:
        return 0.0
        
    points_80_20 = {1: 20.0, 2: 18.0, 3: 14.0, 4: 12.0, 5: 8.0, 6: 6.0, 7: 4.0, 8: 2.0}
    points_90_10 = {1: 10.0, 2: 9.0, 3: 6.0, 4: 5.0, 5: 4.0, 6: 3.0, 7: 2.0, 8: 1.0}
    
    if evaluation_system == '80/20':
        return points_80_20.get(bbbee_level, 0.0)
    else:
        return points_90_10.get(bbbee_level, 0.0)

def calculate_price_score(supplier_price, lowest_price, evaluation_system):
    """Calculates the price score based on PPPFA regulations."""
    if lowest_price <= 0 or supplier_price < lowest_price:
        supplier_price = lowest_price  # Avoid negative price difference if supplier is the lowest
        
    multiplier = 80.0 if evaluation_system == '80/20' else 90.0
    score = multiplier * (1 - (supplier_price - lowest_price) / lowest_price)
    return max(0.0, float(score))

def calculate_total_sa_score(supplier_price, lowest_competing_price, bbbee_level, tender_value_zar, num_competitors=None, evaluation_system_override=None, specific_goals_bbbee_ratio=1.0):
    """Calculates the total preferential procurement score."""
    evaluation_system = evaluation_system_override if evaluation_system_override else get_evaluation_system(tender_value_zar)
    price_score = calculate_price_score(supplier_price, lowest_competing_price, evaluation_system)
    
    raw_bbbee_points = get_bbbee_points(bbbee_level, evaluation_system)
    bbbee_points = raw_bbbee_points * specific_goals_bbbee_ratio
    
    total_score = price_score + bbbee_points
    
    if total_score >= 75:
        competitive_position = 'Strong'
    elif total_score >= 50:
        competitive_position = 'Moderate'
    else:
        competitive_position = 'Weak'
        
    return {
        'evaluation_system': evaluation_system,
        'price_score': round(price_score, 4),
        'bbbee_points': float(bbbee_points),
        'total_score': round(total_score, 4),
        'max_possible_score': 100.0,
        'score_percentage': round(total_score, 4),
        'competitive_position': competitive_position
    }

def adjust_probability_for_sa(base_probability, sa_score_dict, num_competitors=None):
    """Adjusts the base ML probability using the SA preferential score."""
    sa_win_prob = sa_score_dict['score_percentage'] / 100.0
    
    if num_competitors is not None and num_competitors > 0:
        competition_factor = 1.0 / num_competitors
        sa_adjusted = (sa_win_prob * competition_factor) ** 0.5
    else:
        sa_adjusted = sa_win_prob * 0.3
        
    final_prob = (0.6 * base_probability) + (0.4 * sa_adjusted)
    
    # Clip to [0.02, 0.95]
    final_prob = max(0.02, min(0.95, final_prob))
    
    return {
        'base_ml_probability': round(base_probability, 4),
        'sa_score_adjusted': round(sa_adjusted, 4),
        'final_probability': round(final_prob, 4),
        'uplift': round(final_prob - base_probability, 4)
    }

def get_bbbee_recommendation(bbbee_level):
    """Returns advice on B-BBEE positioning."""
    if bbbee_level in [1, 2]:
        return "Your B-BBEE level is highly competitive. You receive maximum preferential points."
    elif bbbee_level in [3, 4]:
        return "Your B-BBEE level is competitive. Consider improving to Level 2 for maximum points."
    elif bbbee_level in [5, 6]:
        return "Your B-BBEE level is below average. Improving your B-BBEE certificate would significantly increase your preferential points."
    elif bbbee_level in [7, 8]:
        return "Your B-BBEE level is non-competitive. Priority action: improve B-BBEE rating before bidding on high-value tenders."
    else:
        return "No B-BBEE certificate detected. You will receive 0 preferential points. This significantly reduces your competitiveness on SA government tenders."

if __name__ == '__main__':
    print("Running self tests...")
    
    # Test 1: Level 2, price R450k, lowest R400k, tender value R2M, 3 competitors
    print("\nTest Case 1:")
    score1 = calculate_total_sa_score(450000, 400000, 2, 2000000, 3)
    print(score1)
    prob1 = adjust_probability_for_sa(0.35, score1, 3)
    print(prob1)
    
    # Test 2: Level 4, price R52M, lowest R48M, tender value R55M, 5 competitors
    print("\nTest Case 2:")
    score2 = calculate_total_sa_score(52000000, 48000000, 4, 55000000, 5)
    print(score2)
    prob2 = adjust_probability_for_sa(0.40, score2, 5)
    print(prob2)
    
    # Test 3: Non-compliant, price R300k, lowest R280k, tender value R1.5M, 4 competitors
    print("\nTest Case 3:")
    score3 = calculate_total_sa_score(300000, 280000, 0, 1500000, 4)
    print(score3)
    prob3 = adjust_probability_for_sa(0.20, score3, 4)
    print(prob3)
    
    print("\nSelf tests complete.")
