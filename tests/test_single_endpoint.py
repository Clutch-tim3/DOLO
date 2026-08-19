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
    """
    An eligible tender with no competing price in it yields no probability.

    This assertion was inverted until the price fabrication was removed. It
    read `win_probability is not None` and `recommendation in [PURSUE, PASS]`,
    and it passed — but only because `pdf_parser` set `lowest_price` to
    `bid_price * 0.9`, so a competing price always existed and the PPPFA score
    could always be computed. `lv_cabling_tender.pdf` states no competitor's
    price, because a sealed-bid tender does not.

    The eligibility verdict, the evaluation system and the B-BBEE points are
    asserted below precisely because they must survive the removal — the fix
    withholds the fabricated number without taking the real signal with it.
    """
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

    # Withheld, not invented.
    assert res["win_probability"] is None
    assert res["sa_analysis"]["adjusted_probability"] is None
    assert res["sa_analysis"]["price_score"] is None
    assert res["sa_analysis"]["competitive_position"] is None
    assert res["recommendation"] is None, "a bid/no-bid call with no score behind it"

    # And the user is told why, rather than seeing a silent blank.
    assert res["sa_analysis"]["price_score_available"] is False
    assert "competing price" in res["sa_analysis"]["price_score_unavailable_reason"].lower()

    # The signal that does not depend on a competitor's price still arrives.
    # Level 1 scores maximum points under either system: 20 under 80/20, 10
    # under 90/10. Which system applies is decided by the tender's value, so
    # the assertion is tied to the system actually chosen rather than assuming
    # one — this fixture currently resolves to 90/10, i.e. it is being valued
    # at R50m or more.
    eval_sys = res["sa_analysis"]["evaluation_system"]
    assert eval_sys in ("80/20", "90/10")
    assert res["sa_analysis"]["bbbee_points"] == (20.0 if eval_sys == "80/20" else 10.0)
    assert res["sa_analysis"]["bbbee_points"] == res["sa_analysis"]["max_bbbee_points"]
    assert res["bbbee_level"] == 1


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
