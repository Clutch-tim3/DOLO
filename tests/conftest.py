import os
import pytest
import sys
from pathlib import Path
import json
import xgboost as xgb
import lightgbm as lgb
from catboost import CatBoostClassifier
import joblib

# Ensure the root directory is in the path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Agent Autofill signs review acknowledgements and export stamps, and fails
# closed when the secret is absent. Supply a fixed test value so the suite
# exercises the signed paths rather than the missing-secret refusal — that
# refusal has its own test, which clears the variable deliberately.
# setdefault, so a real secret in the environment is never overwritten.
os.environ.setdefault("AUTOFILL_STAMP_SECRET", "test-only-not-a-real-secret")

from predict.predict import load_all_artifacts

@pytest.fixture(scope="session")
def loaded_models():
    """Loads all heavy ML artifacts exactly once per test session."""
    return load_all_artifacts()


@pytest.fixture(scope="session")
def auth_headers():
    """
    Headers that authenticate as a real pro_corp user.

    Every company-aware route used to resolve its tenant from an
    `X-Company-ID` header that defaulted to `starter_corp`, so an endpoint test
    could just... call it. That default was the bug, and removing it means the
    tests that exercised those routes have to hold a credential like any other
    client. They authenticate properly rather than being relaxed.

    A bearer token rather than the cookie, because it survives a TestClient
    being constructed at module import time in several of these files.
    """
    import uuid

    from agent import auth

    user = auth.create_user(
        f"suite-{uuid.uuid4().hex[:10]}@example.test", "pro_corp",
        "test-suite-not-a-real-password")
    return {"Authorization": f"Bearer {auth.issue_session(user)}"}

@pytest.fixture
def sample_supplier_profile_new_bidder():
    return {
        'company_name': 'NEW BIDDER PTY LTD',
        'bbbee_level': 1,
        'pit_total_wins': 0,
        'pit_win_rate_overall': 0.0,
        'registered_municipality': 'City of Johannesburg',
        'province': 'Gauteng',
        'has_csd': True,
        'has_cidb': True,
        'has_tax_clearance': True
    }

@pytest.fixture
def sample_supplier_profile_experienced():
    return {
        'company_name': 'EXPERIENCED SUPPLIER LTD',
        'bbbee_level': 2,
        'pit_total_wins': 5,
        'pit_win_rate_overall': 0.35,
        'pit_is_incumbent': 0,
        'registered_municipality': 'City of Tshwane',
        'province': 'Gauteng',
        'has_csd': True,
        'has_cidb': True,
        'has_tax_clearance': True
    }

@pytest.fixture
def sample_supplier_profile_incumbent():
    return {
        'company_name': 'INCUMBENT SUPPLIER LTD',
        'bbbee_level': 1,
        'pit_total_wins': 20,
        'pit_win_rate_overall': 0.55,
        'pit_is_incumbent': 1,
        'pit_buyer_win_count': 3,
        'registered_municipality': 'City of Cape Town',
        'province': 'Western Cape',
        'has_csd': True,
        'has_cidb': True,
        'has_tax_clearance': True
    }

@pytest.fixture
def fixtures_dir():
    return Path(__file__).parent / "fixtures"

@pytest.fixture
def alfred_duma_text():
    return """
ALFRED DUMA LOCAL MUNICIPALITY
TENDER NO: DF 04/2026
Tender value: R 2,500,000
Briefing session: 12 August 2026
Closing date: 24 August 2026
EVALUATION CRITERIA: Functionality
The minimum qualifying score of 80% is required to be eligible for preference points.
Past contract track record: Max points 30
Locality: Must be registered in Alfred Duma municipality.
Preference point system: 80/20
Specific goals: 5 points HDI, 15 points Locality.
"""

@pytest.fixture
def eligible_sample_text():
    return """
STANDARD PROCUREMENT TENDER
TENDER NO: STD-2026
Tender Value: R 1,000,000
Closing date: 20 September 2026
Applicable preference point system 80/20.
Boilerplate: up to R50,000,000
"""
