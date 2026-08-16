import math

SYSTEM_80_20 = '80/20'
SYSTEM_90_10 = '90/10'

#: The PPPFA threshold. Below it a tender is evaluated 80/20, at or above it
#: 90/10. Which one applies changes the B-BBEE points a bidder can earn.
PPPFA_THRESHOLD_ZAR = 50_000_000

#: Why the evaluation system can be unknown, in the words the user should see.
NO_TENDER_VALUE = (
    "The tender's value was not found in the document, so it is not possible to "
    "say whether the 80/20 or the 90/10 preference system applies. The two award "
    "different B-BBEE points for the same level, so neither is assumed here."
)


def get_evaluation_system(tender_value_zar):
    """
    Which PPPFA preference system applies, or None when the value is unknown.

    This returned '80/20' for a missing value. That is an assertion about which
    statute governs a bid, made with no information: 80/20 awards a level 1
    bidder 20 preference points where 90/10 awards 10, so defaulting also
    overstates the bidder's position by double whenever the guess is wrong.

    It was reachable, because `pdf_parser` filled an unparsed tender value with
    `bid_price * 1.2` — so a bid just over R41.7m crossed the R50m threshold on
    a guess and was told the wrong system applied.
    """
    if tender_value_zar is None:
        return None
    return SYSTEM_80_20 if tender_value_zar < PPPFA_THRESHOLD_ZAR else SYSTEM_90_10


def get_bbbee_points(bbbee_level, evaluation_system):
    """
    Preference points for a level under a given system, or None if unknown.

    The `else` branch used to catch everything that was not exactly '80/20',
    so an unrecognised value — or None — silently returned 90/10 points. A
    typo in a system name would have quietly halved every bidder's score.
    """
    if evaluation_system not in (SYSTEM_80_20, SYSTEM_90_10):
        return None

    if bbbee_level is None or bbbee_level == 0:
        return 0.0

    points_80_20 = {1: 20.0, 2: 18.0, 3: 14.0, 4: 12.0, 5: 8.0, 6: 6.0, 7: 4.0, 8: 2.0}
    points_90_10 = {1: 10.0, 2: 9.0, 3: 6.0, 4: 5.0, 5: 4.0, 6: 3.0, 7: 2.0, 8: 1.0}

    table = points_80_20 if evaluation_system == SYSTEM_80_20 else points_90_10
    return table.get(bbbee_level, 0.0)

#: Why a price score can be missing, in the words the user should see.
NO_COMPETING_PRICE = (
    "No competing price was found in the tender document, so the PPPFA price "
    "score cannot be calculated. Bids are sealed, so a tender you are bidding "
    "on does not normally state what anyone else charged. Supply the lowest "
    "competing price yourself if you know it."
)


def calculate_price_score(supplier_price, lowest_price, evaluation_system):
    """
    Price score under PPPFA, or None when there is nothing to compare against.

    Returns None rather than a number when either price is missing. The formula
    is correct; it is only meaningful if `lowest_price` is a real competing
    price. `pdf_parser` used to supply `bid_price * 0.9` here, which made every
    supplier look like they were 11% above the market by construction.
    """
    if lowest_price is None or supplier_price is None:
        return None
    if lowest_price <= 0:
        return None
    # The multiplier below is the system's own weighting, so an unknown system
    # means an unknown score rather than a defaulted one.
    if evaluation_system not in (SYSTEM_80_20, SYSTEM_90_10):
        return None
    if supplier_price < lowest_price:
        supplier_price = lowest_price  # Avoid negative price difference if supplier is the lowest

    multiplier = 80.0 if evaluation_system == SYSTEM_80_20 else 90.0
    score = multiplier * (1 - (supplier_price - lowest_price) / lowest_price)
    return max(0.0, float(score))

def calculate_total_sa_score(supplier_price, lowest_competing_price, bbbee_level, tender_value_zar, num_competitors=None, evaluation_system_override=None, specific_goals_bbbee_ratio=1.0):
    """Calculates the total preferential procurement score."""
    evaluation_system = evaluation_system_override if evaluation_system_override else get_evaluation_system(tender_value_zar)
    price_score = calculate_price_score(supplier_price, lowest_competing_price, evaluation_system)
    
    raw_bbbee_points = get_bbbee_points(bbbee_level, evaluation_system)
    bbbee_points = (None if raw_bbbee_points is None
                    else raw_bbbee_points * specific_goals_bbbee_ratio)

    # Which system applies is not knowable without the tender's value, and the
    # two award different points for the same level — so when it is unknown the
    # B-BBEE points go with it rather than being quoted under an assumed
    # system. This is the one case where nothing at all can be scored.
    if evaluation_system is None:
        return {
            'evaluation_system': None,
            'evaluation_system_unavailable_reason': NO_TENDER_VALUE,
            'price_score': None,
            'price_score_available': False,
            'price_score_unavailable_reason': NO_TENDER_VALUE,
            'bbbee_points': None,
            'max_bbbee_points': None,
            'total_score': None,
            'max_possible_score': 100.0,
            'score_percentage': None,
            'competitive_position': None,
        }

    # B-BBEE points stand on their own: they depend only on the bidder's level
    # and the system, both known here. The price score does not, so when it is
    # missing the totals built on top of it are withheld rather than computed
    # from the half we have.
    if price_score is None:
        return {
            'evaluation_system': evaluation_system,
            'evaluation_system_unavailable_reason': None,
            'price_score': None,
            'price_score_available': False,
            'price_score_unavailable_reason': NO_COMPETING_PRICE,
            'bbbee_points': float(bbbee_points),
            'max_bbbee_points': get_bbbee_points(1, evaluation_system),
            'total_score': None,
            'max_possible_score': 100.0,
            'score_percentage': None,
            'competitive_position': None,
        }

    total_score = price_score + bbbee_points

    if total_score >= 75:
        competitive_position = 'Strong'
    elif total_score >= 50:
        competitive_position = 'Moderate'
    else:
        competitive_position = 'Weak'

    return {
        'evaluation_system': evaluation_system,
        'evaluation_system_unavailable_reason': None,
        'price_score': round(price_score, 4),
        'price_score_available': True,
        'price_score_unavailable_reason': None,
        'bbbee_points': float(bbbee_points),
        'max_bbbee_points': get_bbbee_points(1, evaluation_system),
        'total_score': round(total_score, 4),
        'max_possible_score': 100.0,
        'score_percentage': round(total_score, 4),
        'competitive_position': competitive_position
    }

def adjust_probability_for_sa(base_probability, sa_score_dict, num_competitors=None):
    """
    Adjusts the base ML probability using the SA preferential score.

    Returns a None probability when the PPPFA score could not be calculated.
    Falling back to `base_probability` alone would have been the tempting
    thing, and is wrong: the caller cannot tell that apart from a score that
    genuinely came out neutral, which is how a withheld number becomes an
    asserted one.
    """
    if sa_score_dict.get('score_percentage') is None:
        return {
            'base_ml_probability': round(base_probability, 4) if base_probability is not None else None,
            'sa_score_adjusted': None,
            'final_probability': None,
            'uplift': None,
            'unavailable_reason': sa_score_dict.get(
                'price_score_unavailable_reason', NO_COMPETING_PRICE),
        }

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
