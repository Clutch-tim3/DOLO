import pytest
from predict.eligibility_gate import check_hard_eligibility

def test_alfred_duma_disqualified(alfred_duma_text, sample_supplier_profile_new_bidder):
    res = check_hard_eligibility(alfred_duma_text, sample_supplier_profile_new_bidder)
    assert res['eligible'] is False
    
    reasons = [f['reason'] for f in res['hard_failures']]
    func_fail = any("Functionality gate requires" in r for r in reasons)
    loc_fail = any("Tender requires location" in r for r in reasons)
    
    assert func_fail is True
    assert loc_fail is True

def test_synthetic_eligible_tender_passes(eligible_sample_text, sample_supplier_profile_new_bidder):
    res = check_hard_eligibility(eligible_sample_text, sample_supplier_profile_new_bidder)
    assert res['eligible'] is True
    assert len(res['hard_failures']) == 0

def test_gate_not_always_true_or_always_false(alfred_duma_text, eligible_sample_text, sample_supplier_profile_new_bidder):
    res1 = check_hard_eligibility(alfred_duma_text, sample_supplier_profile_new_bidder)
    res2 = check_hard_eligibility(eligible_sample_text, sample_supplier_profile_new_bidder)
    assert res1['eligible'] != res2['eligible']

def test_low_confidence_still_runs_ml_with_warning(sample_supplier_profile_new_bidder):
    text = "This is a random document with no explicit thresholds."
    res = check_hard_eligibility(text, sample_supplier_profile_new_bidder)
    # The default shouldn't just ban them
    assert res['eligible'] is True

def test_mandatory_registration_gate(eligible_sample_text):
    text = eligible_sample_text + "\\nMust be registered on CSD."
    prof = {'has_csd': False}
    res = check_hard_eligibility(text, prof)
    assert res['eligible'] is False
    assert any("CSD Registration is mandatory but missing." in f['reason'] for f in res['hard_failures'])

def test_briefing_logistics_warning_not_hard_fail(eligible_sample_text, sample_supplier_profile_new_bidder):
    text = eligible_sample_text + "\\nCompulsory briefing in Western Cape."
    prof = sample_supplier_profile_new_bidder
    prof['province'] = 'Gauteng'
    
    res = check_hard_eligibility(text, prof)
    assert res['eligible'] is True
    assert len(res['logistics_warnings']) > 0
    assert any("Compulsory briefing is likely in Western Cape" in w['reason'] for w in res['logistics_warnings'])
