import pytest
import asyncio
import uuid
import shutil
from pathlib import Path
from app import process_batch_job, BATCH_JOBS

@pytest.mark.asyncio
async def test_high_concurrency_state_isolation(tmp_path, loaded_models):
    """
    Simulates 20 concurrent batch job submissions to ensure the BATCH_JOBS
    dictionary and file I/O operations do not contaminate state across users.
    """
    NUM_CONCURRENT_JOBS = 20
    fixtures_dir = Path("tests/fixtures")
    valid_file = fixtures_dir / "lv_cabling_tender.pdf"
    
    # We create a function to submit a single job with a unique UUID
    async def submit_job(job_index):
        job_id = str(uuid.uuid4())
        BATCH_JOBS[job_id] = {
            "status": "processing",
            "total": 1,
            "processed": 0,
            "results": [],
            "filename": f"batch_{job_index}.zip"
        }
        
        # Copy file to isolated tmp directory for this job
        job_tmp_dir = tmp_path / f"job_{job_id}"
        job_tmp_dir.mkdir(parents=True, exist_ok=True)
        dest = job_tmp_dir / f"tender_{job_index}.pdf"
        shutil.copy(valid_file, dest)
        
        # Supplier name unique to the job
        supplier_name = f"SUPPLIER_CO_{job_index}"
        
        await process_batch_job(
            job_id,
            [dest],
            [f"tender_{job_index}.pdf"],
            supplier_name,
            1,
            loaded_models
        )
        
        return job_id, supplier_name, f"tender_{job_index}.pdf"
        
    # Execute 100 jobs simultaneously
    tasks = [submit_job(i) for i in range(NUM_CONCURRENT_JOBS)]
    results = await asyncio.gather(*tasks)
    
    # Verify strict isolation
    assert len(BATCH_JOBS) >= NUM_CONCURRENT_JOBS
    
    for job_id, expected_supplier, expected_filename in results:
        job_data = BATCH_JOBS[job_id]

        # Ensure completion
        assert job_data["status"] == "complete"
        assert job_data["processed"] == 1
        assert len(job_data["results"]) == 1

        res = job_data["results"][0]

        # Very critical: ensure this job's result was not overwritten by another
        # thread. The filename is unique per job, so a mismatch here IS the
        # collision this test exists to catch.
        #
        # This used to be asserted as `win_probability is not None`, which was
        # never a test of isolation — it only ever showed that the pipeline
        # returned a number, and it returned one for every input because the
        # number was fabricated. `expected_supplier` was captured and never
        # compared to anything at all.
        assert res["filename"] == expected_filename, (
            f"job for {expected_filename} carries {res['filename']} — results crossed between jobs"
        )
        assert job_data["filename"].startswith("batch_"), "job metadata was overwritten"

        # The pipeline ran to completion rather than falling into the error path.
        assert res["processing_error"] is None, res["processing_error"]
        assert "ERROR" not in (res.get("recommendation") or "")

        # Per-job output that does not depend on a competing price being present,
        # so this stays a concurrency test rather than a scoring one.
        assert res["preferential_framework"] in ("80/20", "90/10")
        assert res["bbbee_points"] is not None
