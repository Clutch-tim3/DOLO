import pytest
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)

def test_single_prediction_full_flow(fixtures_dir):
    file_path = fixtures_dir / "alfred_duma.pdf"
    with open(file_path, "rb") as f:
        response = client.post(
            "/api/tender/submit",
            files={"document": ("alfred_duma.pdf", f, "application/pdf")},
            data={"supplier_name": "TEST SUPPLIER", "bbbee_level": 1}
        )
        
    assert response.status_code == 200
    data = response.json()
    assert data["disqualified"] is True
    assert "win_probability" in data

def test_single_prediction_eligible_tender(fixtures_dir):
    file_path = fixtures_dir / "lv_cabling_tender.pdf"
    with open(file_path, "rb") as f:
        response = client.post(
            "/api/tender/submit",
            files={"document": ("lv_cabling_tender.pdf", f, "application/pdf")},
            data={"supplier_name": "TEST SUPPLIER", "bbbee_level": 1}
        )
        
    assert response.status_code == 200
    res = response.json()
    
    assert res["disqualified"] is False
    assert res["win_probability"] is not None
    assert res["sa_analysis"]["adjusted_probability"] is not None
    assert res["recommendation"] in ["PURSUE", "WEAK POSITION", "NO BID"]
