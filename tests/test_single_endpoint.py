import pytest
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)

# These calls carry a real credential now. /api/tender/submit used to resolve
# its tenant from an X-Company-ID header that defaulted to "starter_corp", so an
# anonymous POST was served as that company; it is a 401 now. The `auth_headers`
# fixture in conftest.py provisions a pro_corp user and returns a bearer token.
# The prediction itself is unaffected: "sailor" is in every tier's model access,
# so the assertions below are testing exactly what they tested before.


def test_single_prediction_full_flow(fixtures_dir, auth_headers):
    file_path = fixtures_dir / "alfred_duma.pdf"
    with open(file_path, "rb") as f:
        response = client.post(
            "/api/tender/submit",
            files={"tender_file": ("alfred_duma.pdf", f, "application/pdf")},
            data={"supplier_name": "TEST SUPPLIER", "bbbee_level": 1},
            headers=auth_headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["disqualified"] is True
    assert "win_probability" in data


def test_single_prediction_eligible_tender(fixtures_dir, auth_headers):
    file_path = fixtures_dir / "lv_cabling_tender.pdf"
    with open(file_path, "rb") as f:
        response = client.post(
            "/api/tender/submit",
            files={"tender_file": ("lv_cabling_tender.pdf", f, "application/pdf")},
            data={"supplier_name": "TEST SUPPLIER", "bbbee_level": 1},
            headers=auth_headers,
        )

    assert response.status_code == 200
    res = response.json()

    assert res["disqualified"] is False
    assert res["win_probability"] is not None
    assert res["sa_analysis"]["adjusted_probability"] is not None
    assert res["recommendation"] in ["PURSUE", "PASS"]


def test_the_same_call_without_a_credential_is_refused(fixtures_dir):
    """
    The route above is the one an anonymous caller used to reach as
    "starter_corp". Pinned here so the credential in the tests above cannot be
    quietly dropped again.
    """
    file_path = fixtures_dir / "alfred_duma.pdf"
    with open(file_path, "rb") as f:
        response = client.post(
            "/api/tender/submit",
            files={"tender_file": ("alfred_duma.pdf", f, "application/pdf")},
            data={"supplier_name": "TEST SUPPLIER", "bbbee_level": 1},
        )
    assert response.status_code == 401
