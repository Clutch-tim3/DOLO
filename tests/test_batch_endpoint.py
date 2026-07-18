import pytest
import uuid
import json
import asyncio
from app import process_batch_job, BATCH_JOBS
from fastapi.testclient import TestClient
from app import app
from models.pdf_parser import parse_tender_document

@pytest.mark.asyncio
async def test_batch_three_different_tenders_produce_different_results(fixtures_dir, tmp_path):
    job_id = str(uuid.uuid4())
    BATCH_JOBS[job_id] = {
        "status": "processing",
        "total": 3,
        "processed": 0,
        "results": [],
        "filename": "batch.zip"
    }
    
    # Copy to tmp_path
    import shutil
    files = []
    for f in ["alfred_duma.pdf", "lv_cabling_tender.pdf", "rfb_001_comms.docx"]:
        dest = tmp_path / f
        shutil.copy(fixtures_dir / f, dest)
        files.append(dest)
    filenames = ["alfred_duma.pdf", "lv_cabling_tender.pdf", "rfb_001_comms.docx"]
    
    await process_batch_job(job_id, files, filenames, "TEST SUPPLIER", 1)
    
    results = BATCH_JOBS[job_id]["results"]
    assert len(results) == 3
    
    # Check uniqueness of IDs
    ids = [r["tender_identifier"] for r in results]
    assert len(set(ids)) == 3
    
    # Find results
    alfred = next(r for r in results if r["filename"] == "alfred_duma.pdf")
    lv = next(r for r in results if r["filename"] == "lv_cabling_tender.pdf")
    comms = next(r for r in results if r["filename"] == "rfb_001_comms.docx")
    
    assert alfred["disqualified"] is True
    
    assert abs(lv["sa_adjusted_probability"] - comms["sa_adjusted_probability"]) > 0.001
    
    for r in results:
        assert r["extraction_completeness"] >= 0.8

@pytest.mark.asyncio
async def test_batch_no_shared_state_between_files(fixtures_dir, tmp_path):
    job_id = str(uuid.uuid4())
    BATCH_JOBS[job_id] = {
        "status": "processing",
        "total": 2,
        "processed": 0,
        "results": [],
        "filename": "batch.zip"
    }
    
    import shutil
    files = []
    for f in ["alfred_duma.pdf", "lv_cabling_tender.pdf"]:
        dest = tmp_path / f
        shutil.copy(fixtures_dir / f, dest)
        files.append(dest)
    filenames = ["alfred_duma.pdf", "lv_cabling_tender.pdf"]
    
    await process_batch_job(job_id, files, filenames, "TEST SUPPLIER", 1)
    
    results = BATCH_JOBS[job_id]["results"]
    assert len(results) == 2
    
    r1 = results[0]
    r2 = results[1]
    
    # If they are eligible, they should have their own prices used for sa scoring.
    # We can check parsed_tender_value
    assert r1.get("parsed_tender_value") != r2.get("parsed_tender_value")

@pytest.mark.asyncio
async def test_batch_partial_failure_does_not_kill_other_results(fixtures_dir, tmp_path):
    job_id = str(uuid.uuid4())
    BATCH_JOBS[job_id] = {
        "status": "processing",
        "total": 3,
        "processed": 0,
        "results": [],
        "filename": "batch.zip"
    }
    
    import shutil
    files = []
    for f in ["lv_cabling_tender.pdf", "malformed.pdf", "rfb_001_comms.docx"]:
        dest = tmp_path / f
        shutil.copy(fixtures_dir / f, dest)
        files.append(dest)
    filenames = ["lv_cabling_tender.pdf", "malformed.pdf", "rfb_001_comms.docx"]
    
    await process_batch_job(job_id, files, filenames, "TEST SUPPLIER", 1)
    
    results = BATCH_JOBS[job_id]["results"]
    assert len(results) == 3
    
    malformed_res = next(r for r in results if r["filename"] == "malformed.pdf")
    assert malformed_res["recommendation"] in ["ERROR - Unreadable document", "DISQUALIFIED"]
    
    valid_res = next(r for r in results if r["filename"] == "lv_cabling_tender.pdf")
    assert valid_res["win_probability"] is not None

@pytest.mark.asyncio
async def test_batch_docx_pdf_mixed_no_crash(fixtures_dir, tmp_path):
    job_id = str(uuid.uuid4())
    BATCH_JOBS[job_id] = {
        "status": "processing",
        "total": 2,
        "processed": 0,
        "results": [],
        "filename": "batch.zip"
    }
    
    import shutil
    files = []
    for f in ["lv_cabling_tender.pdf", "rfb_001_comms.docx"]:
        dest = tmp_path / f
        shutil.copy(fixtures_dir / f, dest)
        files.append(dest)
    filenames = ["lv_cabling_tender.pdf", "rfb_001_comms.docx"]
    
    # Should not raise exception
    await process_batch_job(job_id, files, filenames, "TEST SUPPLIER", 1)
    results = BATCH_JOBS[job_id]["results"]
    assert len(results) == 2
    assert results[0]["win_probability"] is not None
    assert results[1]["win_probability"] is not None

client = TestClient(app)

def test_batch_result_matches_single_prediction_result(fixtures_dir, tmp_path):
    file_path = fixtures_dir / "lv_cabling_tender.pdf"
    
    # Call single endpoint
    with open(file_path, "rb") as f:
        response = client.post(
            "/api/tender/submit",
            files={"tender_file": ("lv_cabling_tender.pdf", f, "application/pdf")},
            data={"supplier_name": "TEST SUPPLIER", "bbbee_level": 1}
        )
    
    single_res = response.json()
    
    # Run batch locally
    job_id = str(uuid.uuid4())
    BATCH_JOBS[job_id] = {
        "status": "processing",
        "total": 1,
        "processed": 0,
        "results": [],
        "filename": "batch.zip"
    }
    
    import shutil
    dest = tmp_path / "lv_cabling_tender.pdf"
    shutil.copy(file_path, dest)
    
    # Async to sync
    loop = asyncio.get_event_loop()
    loop.run_until_complete(process_batch_job(job_id, [dest], ["lv_cabling_tender.pdf"], "TEST SUPPLIER", 1))
    
    batch_res = BATCH_JOBS[job_id]["results"][0]
    
    assert single_res["win_probability"] == batch_res["sa_adjusted_probability"]
    assert single_res["sa_analysis"]["final_probability"] == batch_res["sa_adjusted_probability"]
    assert single_res["recommendation"] == batch_res["recommendation"]
