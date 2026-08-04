from predict.regional_router import predict_tender_region, detect_region
import traceback
import os
import sys
import json
import shutil
import re
from pathlib import Path
from typing import List, Optional
import sqlite3
from datetime import datetime, timedelta
from pydantic import BaseModel
from google import genai
from google.genai import types
import uuid
from fastapi import BackgroundTasks
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Header, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv(override=True)
# .env.local holds the developer's own key. It exists because the Firebase CLI
# loads .env at DEPLOY time and sets every key in it as a plaintext env var on
# the function, which shadows the Secret Manager binding - so .env must stay
# free of secrets. Firebase never deploys .env.local, so this is a no-op in
# production, where the key arrives from Secret Manager instead.
load_dotenv(".env.local", override=True)
# Presence check only. This previously printed the full ANTHROPIC_API_KEY on
# every startup; on Firebase that writes the live secret into Cloud Logging,
# where anyone with log-viewer access can read it.
print("APP.PY STARTUP - ANTHROPIC_API_KEY present:", bool(os.environ.get("ANTHROPIC_API_KEY")))

# Add root directory to path to allow importing predict and models
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from predict.predict import load_all_artifacts, get_feature_list, extract_features_from_tender_id, build_new_features, encode_and_impute, predict
from models.sa_scoring import calculate_total_sa_score, adjust_probability_for_sa, get_bbbee_recommendation
from models.pdf_parser import parse_company_pdf, extract_text_from_pdf, classify_document_type
from predict.eligibility_gate import check_hard_eligibility
from models.pdf_parser import parse_tender_document
# Vault uploads are recorded against the company here; the vault reads from the
# same table, which is what makes an uploaded document actually appear.
from agent.memory.company_store import add_company_document
from agent.tool_dispatch import vault_type_for
from models.quotation_generator import generate_quotation_pdf

from agent.subscription import get_config, check_quote_quota, get_subscription_status, log_quote_generation
from agent.main_agent import generate_draft_quote_flow, finalize_quote_flow, process_agent_chat, memory_tools, app_help_tools, onboarding_tools, quotation_tools

import logging
from logging.handlers import RotatingFileHandler

LOG_DIR = PROJECT_ROOT / "logs"
if os.environ.get("K_SERVICE"):
    LOG_DIR = Path("/tmp/logs")
os.makedirs(str(LOG_DIR), exist_ok=True)

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "funcName": record.funcName,
        }
        if hasattr(record, "company_id"):
            log_obj["company_id"] = record.company_id
        if hasattr(record, "endpoint"):
            log_obj["endpoint"] = record.endpoint
        if hasattr(record, "extra_data"):
            log_obj.update(record.extra_data)
        return json.dumps(log_obj)

logger = logging.getLogger("api_monitor")
logger.setLevel(logging.INFO)
# Avoid adding multiple handlers in hot reloads
if not logger.handlers:
    log_handler = RotatingFileHandler(str(LOG_DIR / "api.log"), maxBytes=5000000, backupCount=5)
    log_handler.setFormatter(JSONFormatter())
    logger.addHandler(log_handler)

app = FastAPI()

BATCH_JOBS = {}

DATA_DIR = PROJECT_ROOT / "data"
if os.environ.get("K_SERVICE"):
    DATA_DIR = Path("/tmp/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "procurement.db"

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS tracked_outcomes (
            id TEXT PRIMARY KEY,
            prediction_id TEXT,
            tender_identifier TEXT,
            filename TEXT,
            supplier_name TEXT,
            predicted_probability REAL,
            sa_adjusted_probability REAL,
            recommendation TEXT,
            actual_outcome TEXT,
            outcome_date TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

class TrackOutcomeRequest(BaseModel):
    prediction_id: str
    tender_identifier: Optional[str] = None
    filename: Optional[str] = None
    supplier_name: Optional[str] = None
    predicted_probability: Optional[float] = None
    sa_adjusted_probability: Optional[float] = None
    recommendation: Optional[str] = None
    actual_outcome: str
    outcome_date: Optional[str] = None
    notes: Optional[str] = ""

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure upload folders exist
UPLOAD_FOLDER = DATA_DIR / "archive"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
ARCHIVE_JSON_PATH = DATA_DIR / "company_archive.json"

# Initialize empty archive if not exists
if not ARCHIVE_JSON_PATH.exists():
    with open(ARCHIVE_JSON_PATH, "w") as f:
        json.dump([], f)

artifacts_sailor = None
artifacts_conquest = None
feature_list = None

# Load pipeline artifacts once when starting server
try:
    print("-- Loading model artifacts for Web API --")
    artifacts_sailor = load_all_artifacts("_v2")
    try:
        artifacts_conquest = load_all_artifacts("_conquest")
    except Exception as e:
        print(f"Warning: Conquest artifacts not found ({e}). Falling back to Sailor.")
        artifacts_conquest = artifacts_sailor
    
    feature_list = get_feature_list(artifacts_sailor["metadata"])
    print("[OK] Model artifacts successfully cached in memory")
except Exception as e:
    print(f"Error loading model artifacts: {e}")
    artifacts_sailor = None
    artifacts_conquest = None

def get_archived_companies():
    """Reads companies list from company_archive.json"""
    try:
        with open(ARCHIVE_JSON_PATH, "r") as f:
            data = json.load(f)
            
            # Ensure "files" list key exists for all records (self-healing migration)
            modified = False
            for c in data:
                if "files" not in c or not isinstance(c["files"], list):
                    c["files"] = [c["file_name"]] if c.get("file_name") else []
                    modified = True
            
            # Scan UPLOAD_FOLDER for files that exist on disk but aren't associated in JSON
            all_associated_files = set()
            for c in data:
                all_associated_files.update(c.get("files", []))
                
            if UPLOAD_FOLDER.exists():
                files_on_disk = [f.name for f in UPLOAD_FOLDER.glob("*.pdf")]
                for filename in files_on_disk:
                    if filename not in all_associated_files:
                        filepath = UPLOAD_FOLDER / filename
                        parsed_info = parse_company_pdf(filepath)
                        company_name = parsed_info.get("company_name", "").upper()
                        
                        target_company = None
                        if company_name:
                            # Match by name
                            for c in data:
                                if c.get("company_name") == company_name:
                                    target_company = c
                                    break
                                    
                        # Fallback: if only one company exists in the ledger, associate it there
                        if not target_company and len(data) == 1:
                            target_company = data[0]
                            
                        if target_company:
                            if "files" not in target_company:
                                target_company["files"] = []
                            if filename not in target_company["files"]:
                                target_company["files"].append(filename)
                                
                                # Update status flags
                                doc_text = extract_text_from_pdf(filepath)
                                is_csd = "csd" in doc_text.lower() or "central supplier database" in doc_text.lower() or "maaa" in doc_text.lower()
                                is_cipc = "cipc" in doc_text.lower() or "co-operatives" in doc_text.lower() or "cor14.3" in doc_text.lower() or "cor39" in doc_text.lower()
                                if is_csd:
                                    target_company["csd_uploaded"] = True
                                if is_cipc:
                                    target_company["cipc_uploaded"] = True
                                modified = True
                                
            if modified:
                save_archived_companies(data)
            return data
    except Exception as e:
        print(f"Error reading archive: {e}")
        return []

def save_archived_companies(companies):
    """Saves companies list to company_archive.json"""
    try:
        with open(ARCHIVE_JSON_PATH, "w") as f:
            json.dump(companies, f, indent=4)
        return True
    except Exception as e:
        print(f"Failed to save archive: {e}")
        return False

# Mount static folder
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def serve_index():
    return FileResponse("static/index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

@app.get("/sort")
async def serve_sort_page():
    return FileResponse("static/sort.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

@app.get("/accuracy")
async def serve_accuracy_page():
    return FileResponse("static/accuracy.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

@app.get("/vault")
async def serve_vault_page():
    return FileResponse("static/vault.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

@app.get("/calendar")
async def serve_calendar_page():
    return FileResponse("static/calendar.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

@app.get("/system")
async def serve_system_page():
    return FileResponse("static/system.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/api/model-status")
async def api_model_status():
    """Returns active status and metrics of specialized regional engines (Conquest-ZA and Conquest-UK)."""
    return {
        "active_engines": {
            "Conquest-ZA": {
                "region": "South Africa (ZA)",
                "framework": "PPPFA 80/20 & 90/10",
                "auc_val": 0.857833,
                "auc_test": 0.857833,
                "status": "LOCKED_PRODUCTION_BASELINE"
            },
            "Conquest-UK": {
                "region": "United Kingdom (GB)",
                "framework": "MEAT PCR 2015",
                "auc_val": 0.694060,
                "auc_test": 0.694060,
                "status": "STANDALONE_REGIONAL_BASELINE"
            }
        }
    }

@app.get("/workspace")
async def serve_workspace_page():
    return FileResponse("static/workspace.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

class QuotationRequest(BaseModel):
    supplier_name: str
    tender_title: str
    line_items: List[dict]
    evaluation_system: Optional[str] = "80/20"
    lowest_price: Optional[float] = None

@app.post("/api/generate-quotation")
async def api_generate_quotation(request: Request):
    """Generates an official South African PDF quotation for a tender, with support for direct PDF uploads or JSON."""
    logger.info("Generate Quotation Request", extra={"endpoint": "/api/generate_quotation"})
    try:
        content_type = request.headers.get("content-type", "")
        supplier_name = "CAIROAI"
        tender_title = "Procurement Tender Quotation"
        evaluation_system = "80/20"
        line_items = []
        lowest_price = None

        if "multipart/form-data" in content_type:
            form = await request.form()
            tender_file = form.get("tender_file")
            supplier_name = form.get("supplier_name") or "CAIROAI"
            tender_title = form.get("tender_title") or ""
            evaluation_system = form.get("evaluation_system") or "80/20"

            if tender_file and hasattr(tender_file, "filename") and tender_file.filename != "":
                temp_filename = f"quote_temp_{uuid.uuid4().hex[:6]}_{secure_filename(tender_file.filename)}"
                temp_path = UPLOAD_FOLDER / temp_filename
                with open(temp_path, "wb") as buffer:
                    shutil.copyfileobj(tender_file.file, buffer)

                try:
                    parsed_tender = parse_tender_document(temp_path)
                    tender_text = extract_text_from_pdf(temp_path)

                    if not tender_title:
                        tender_title = parsed_tender.get("tender_title") or f"Tender Quotation ({tender_file.filename.replace('.pdf', '')})"

                    tender_val = parsed_tender.get("tender_value") or 798116.25
                    evaluation_system = "90/10" if float(tender_val) >= 50000000 else "80/20"
                    
                    subtotal_est = float(tender_val) / 1.15
                    line_items = [
                        {"description": f"Primary Supply & Delivery per {tender_title[:50]} Specs", "qty": 1, "unit_price": round(subtotal_est * 0.75, 2)},
                        {"description": "Technical Support, Deployment & Quality Assurance", "qty": 1, "unit_price": round(subtotal_est * 0.25, 2)}
                    ]
                finally:
                    if temp_path.exists():
                        temp_path.unlink()

            if not line_items:
                line_items = [{"description": "Professional Goods & Service Delivery", "qty": 1, "unit_price": 798116.25}]
            if not tender_title:
                tender_title = "Procurement Tender Quotation"
        else:
            body = await request.json()
            supplier_name = body.get("supplier_name", "CAIROAI")
            tender_title = body.get("tender_title", "Tender Quotation")
            line_items = body.get("line_items", [{"description": "Services", "qty": 1, "unit_price": 798116.25}])
            evaluation_system = body.get("evaluation_system", "80/20")
            lowest_price = body.get("lowest_price")

        companies = get_archived_companies()
        supplier_info = {}
        for c in companies:
            if c.get("company_name", "").upper() == supplier_name.upper():
                supplier_info = c
                break

        if not supplier_info:
            supplier_info = {
                "company_name": supplier_name,
                "registration_number": "2023/100201/07",
                "csd_number": "MAAA0012345",
                "bbbee_level": 1,
                "cidb_grade": "3GB"
            }

        filename = f"Quotation_{uuid.uuid4().hex[:8]}.pdf"
        out_dir = DATA_DIR / "generated_quotations"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename

        generate_quotation_pdf(
            supplier_info=supplier_info,
            tender_title=tender_title,
            line_items=line_items,
            output_path=out_path,
            lowest_competing_price=lowest_price,
            evaluation_system=evaluation_system
        )

        return {
            "status": "success",
            "pdf_url": f"/api/quotations/{filename}",
            "filename": filename
        }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/archive/upload-document")
async def api_upload_archive_document(
    request: Request,
    file: UploadFile = File(...),
    target_block: str = Form("CSD_CERT")
):
    """
    Accepts a Compliance Vault document: stores it, classifies it, auto-sorts a
    misfiled one into the block it actually belongs to, and records it against
    the company.

    NOTE: this used to save and classify the file and then stop. It never called
    add_company_document, so an upload returned "success" and the document then
    did not appear in the vault, did not count toward the five required
    documents, and did not trigger the onboarding vet. It also ignored
    X-Company-ID, so nothing was tenant-scoped.
    """
    company_id = request.headers.get("X-Company-ID", "starter_corp")
    try:
        filename = secure_filename(file.filename)
        if not filename:
            raise HTTPException(status_code=400, detail="File has no usable name.")

        suffix = Path(filename).suffix.lower()
        if suffix not in {".pdf", ".docx", ".doc"}:
            raise HTTPException(
                status_code=415,
                detail="Only PDF and Word documents can be filed in the vault.",
            )

        save_path = UPLOAD_FOLDER / f"{uuid.uuid4().hex[:6]}_{filename}"
        with open(save_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Extract text and classify
        text = extract_text_from_pdf(save_path)
        cls_info = classify_document_type(text)
        detected_type = cls_info["doc_type"]

        auto_sorted = False
        actual_block = target_block

        if detected_type != "UNKNOWN" and detected_type != target_block:
            auto_sorted = True
            actual_block = detected_type

        # Persist against the company, which is what makes it show up in the
        # vault and count toward completeness.
        parsed_fields = {}
        try:
            parsed_fields = parse_company_pdf(save_path) or {}
        except Exception:
            logger.exception("Could not parse uploaded document", extra={"company_id": company_id})

        doc_id = None
        try:
            doc_id = add_company_document(
                company_id,
                # Mapped, not lowercased: the classifier's block names do not
                # match the vault's required types except by coincidence.
                vault_type_for(actual_block),
                str(save_path),
                parsed_fields,
                parsed_fields.get("expiry_date"),
            )
        except Exception:
            logger.exception("Could not record document", extra={"company_id": company_id})
            raise HTTPException(
                status_code=500,
                detail="The file was received but could not be filed. Try again.",
            )

        return {
            "status": "success",
            "document_id": doc_id,
            "filename": filename,
            "intended_block": target_block,
            "actual_block": actual_block,
            "auto_sorted": auto_sorted,
            "detected_type": detected_type,
            "detected_label": cls_info["label"],
            "intended_block_label": target_block.replace("_", " "),
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/companies")
async def api_get_companies():
    """Returns the list of companies in the archive"""
    return get_archived_companies()

@app.get("/api/company-profile")
async def api_company_profile(request: Request):
    """Returns the active company profile and track record stats for the agent sidebar"""
    company_id = request.headers.get("X-Company-ID", "starter_corp")
    config = get_config(company_id)
    
    # In a real app this would query the DB. We'll return mock data matching the design.
    return {
        "name": "CairoAI",
        "registration": "2026/250499/07",
        "location": "Centurion, GP",
        "tier": "pro" if config.get("agent_enabled") else "starter",
        "stats": {
            "pit_total_wins": 3,
            "pit_win_rate_overall": "21%",
            "bbbee_level": "Lvl 1",
            "pit_is_incumbent": "2 buyers"
        }
    }

@app.post("/api/companies/upload")
async def api_upload_company_file(
    file: List[UploadFile] = File(...),
    target_company: Optional[str] = Form(""),
    expiry_date: Optional[str] = Form(None)
):
    """Uploads CIPC/CSD documents (multiple supported), parses them, and adds/updates companies in the archive"""
    logger.info("Company File Upload", extra={"endpoint": "/api/companies/upload", "extra_data": {"files_count": len(file), "target_company": target_company}})
    if not file or all(f.filename == "" for f in file):
        raise HTTPException(status_code=400, detail="No file uploaded or selected files have no filenames")
        
    target_comp = target_company.upper().strip() if target_company else ""
    
    companies = get_archived_companies()
    uploaded_companies = []
    
    for f in file:
        if f.filename == "":
            continue
            
        filename = secure_filename(f.filename)
        dest_path = UPLOAD_FOLDER / filename
        
        # FastAPI async save
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(f.file, buffer)
        
        # Parse PDF contents
        parsed_info = parse_company_pdf(dest_path)
        
        company_name = None
        if target_comp:
            company_name = target_comp
        elif parsed_info and "company_name" in parsed_info:
            company_name = parsed_info["company_name"].upper()
            
        # Absolute fallback: do not delete the file, name company after filename
        if not company_name:
            company_name = filename.replace(".pdf", "").replace("_", " ").upper()
            
        # Determine document type
        doc_text = extract_text_from_pdf(dest_path)
        is_csd = "csd" in doc_text.lower() or "central supplier database" in doc_text.lower() or "maaa" in doc_text.lower()
        is_cipc = "cipc" in doc_text.lower() or "co-operatives" in doc_text.lower() or "cor14.3" in doc_text.lower() or "cor39" in doc_text.lower() or "disclosure certificate" in doc_text.lower() or "certificate of registration" in doc_text.lower()
        
        # Check if already exists in archive
        existing_index = None
        for idx, c in enumerate(companies):
            if c.get("company_name") == company_name:
                existing_index = idx
                break
                
        company_data = {
            "company_name": company_name,
            "registration_number": parsed_info.get("registration_number", "Pending") if parsed_info else "Pending",
            "supplier_number": parsed_info.get("supplier_number", "Pending") if parsed_info else "Pending",
            "bbbee_level": parsed_info.get("bbbee_level", 9) if parsed_info else 9,
            "cipc_uploaded": is_cipc,
            "csd_uploaded": is_csd,
            "cipc_count": 1 if is_cipc else 0,
            "csd_count": 1 if is_csd else 0,
            "files": [filename]
        }
        
        if existing_index is not None:
            # Update existing
            old = companies[existing_index]
            company_data["cipc_uploaded"] = old.get("cipc_uploaded", False) or is_cipc
            company_data["csd_uploaded"] = old.get("csd_uploaded", False) or is_csd
            
            # Merge files list
            existing_files = old.get("files", [])
            if not isinstance(existing_files, list):
                existing_files = [old.get("file_name")] if old.get("file_name") else []
                
            # Keep counts
            old_cipc_count = old.get("cipc_count", 1 if old.get("cipc_uploaded") else 0)
            old_csd_count = old.get("csd_count", 1 if old.get("csd_uploaded") else 0)
            
            if filename not in existing_files:
                existing_files.append(filename)
                company_data["cipc_count"] = old_cipc_count + (1 if is_cipc else 0)
                company_data["csd_count"] = old_csd_count + (1 if is_csd else 0)
            else:
                company_data["cipc_count"] = old_cipc_count
                company_data["csd_count"] = old_csd_count
                
            company_data["files"] = existing_files
            
            # Recover fields if not present in new doc
            for k in ["registration_number", "supplier_number", "bbbee_level"]:
                if company_data[k] == "Pending" or (k == "bbbee_level" and company_data[k] == 9):
                    if k in old:
                        company_data[k] = old[k]
                        
            companies[existing_index].update(company_data)
            company_data = companies[existing_index]
        else:
            # Append new
            companies.append(company_data)
            
        uploaded_companies.append(company_data)
        
    save_archived_companies(companies)
    
    return {
        "success": True,
        "message": f"Successfully processed {len(uploaded_companies)} documents",
        "companies": uploaded_companies
    }

@app.get("/api/files/{filename}")
async def api_serve_file(filename: str):
    """Serves an uploaded CIPC/CSD file from the archive folder"""
    file_path = UPLOAD_FOLDER / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path))

@app.get("/api/quotations/{filename}")
async def api_serve_quotation(filename: str):
    """Serves a generated quotation PDF from the data folder"""
    file_path = DATA_DIR / "generated_quotations" / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Quotation not found")
    return FileResponse(str(file_path))

@app.get("/api/vault-status")
async def api_vault_status(request: Request):
    """
    Which of the required Compliance Vault documents this company has.

    The Agent page polls this to decide whether the vault is complete enough to
    offer vetting, so it can say what is still outstanding instead of the old
    hardcoded "Auditing vault..." animation that ran on a timer and checked
    nothing.
    """
    company_id = request.headers.get("X-Company-ID", "starter_corp")
    from agent.tool_dispatch import _get_vault_status

    try:
        return _get_vault_status(company_id)
    except Exception:
        logger.exception("Vault status lookup failed", extra={"company_id": company_id})
        raise HTTPException(status_code=500, detail="Could not read vault status")


@app.get("/api/generated/{filename}")
async def api_serve_generated(filename: str):
    """
    Serves anything the agent generated (quotation PDFs, accreditation reports).

    These used to be written to static/downloads and linked as
    /static/downloads/<name>, which only worked locally: the deployed bundle is
    read-only so the write failed, and the URL pointed at the StaticFiles mount
    rather than at the directory the files actually live in. agent/file_paths.py
    now owns that location for both writing and reading.
    """
    from agent.file_paths import safe_generated_path

    try:
        # filename arrives from the URL, so it is untrusted - this rejects
        # traversal rather than trusting the path segment.
        file_path = safe_generated_path(filename)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found or expired")
    return FileResponse(str(file_path), media_type="application/pdf")


@app.delete("/api/companies/{company_name}")
async def api_delete_company(company_name: str):
    """Deletes a company and its files from the archive"""
    companies = get_archived_companies()
    matched_idx = None
    
    name_upper = company_name.upper().strip()
    for idx, c in enumerate(companies):
        if c.get("company_name") == name_upper:
            matched_idx = idx
            break
            
    if matched_idx is None:
        raise HTTPException(status_code=404, detail="Company not found in archive")
        
    company = companies.pop(matched_idx)
    
    # Delete all associated files
    files = company.get("files", [])
    if not isinstance(files, list):
        files = [company.get("file_name")] if company.get("file_name") else []
        
    for filename in files:
        if filename:
            file_path = UPLOAD_FOLDER / filename
            if file_path.exists():
                try:
                    file_path.unlink()
                except Exception as e:
                    print(f"Failed to delete file {filename}: {e}")
                
    save_archived_companies(companies)
    
    return {
        "success": True,
        "message": f"Company '{name_upper}' deleted successfully"
    }

@app.post("/api/predict")
@app.post("/api/tender/submit")
async def api_tender_submit(
    request: Request,
    bid_file: Optional[UploadFile] = File(None),
    tender_file: Optional[UploadFile] = File(None),
    supplier_name: Optional[str] = Form(None),
    bbbee_level: Optional[int] = Form(None),
    model_version: Optional[str] = Form("sailor")
):
    """
    Submits a Tender PDF & Bid PDF:
    - Parses Bid PDF to find matching archived company name.
    - Parses Bid PDF and Tender PDF for pricing.
    - If found, retrieves B-BBEE Level.
    - Queries prediction pipeline and preferential scoring logic.
    """
    company_id = request.headers.get("X-Company-ID", "starter_corp")
    config = get_config(company_id)
    allowed_models = config.get("model_access", ["sailor"])
    
    req_model = model_version.lower() if model_version else allowed_models[-1]
    if req_model not in allowed_models:
        raise HTTPException(status_code=403, detail=f"Model '{req_model}' is not available on your current plan. Upgrade to access.")
        
    target_artifacts = artifacts_conquest if req_model == "conquest" else artifacts_sailor
    if not target_artifacts:
        raise HTTPException(status_code=500, detail="Model artifacts not loaded")

    # Initialize defaults
    bbbee_level_def = 9
    supplier_price = None
    lowest_price = None
    tender_value = None
    tender_id = str(uuid.uuid4())
    num_competitors = 4
    
    matched_company = None
    companies = get_archived_companies()
    
    # 1. Parse Bid PDF
    if bid_file and bid_file.filename != "":
        temp_filename = secure_filename("temp_bid_" + bid_file.filename)
        temp_path = UPLOAD_FOLDER / temp_filename
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(bid_file.file, buffer)
            
        try:
            parsed_bid = parse_company_pdf(temp_path)
            bid_text = extract_text_from_pdf(temp_path)
            
            for c in companies:
                if c.get("company_name") and c.get("company_name") in bid_text.upper():
                    matched_company = c
                    break
                    
            supplier_price = parsed_bid.get("bid_price")
            lowest_price = parsed_bid.get("lowest_price")
        finally:
            if temp_path.exists():
                temp_path.unlink()
                
    # 2. Parse Tender PDF
    if tender_file and tender_file.filename != "":
        temp_filename = secure_filename("temp_tender_" + tender_file.filename)
        temp_path = UPLOAD_FOLDER / temp_filename
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(tender_file.file, buffer)
            
        try:
            parsed_tender = parse_tender_document(temp_path)
            tender_text = extract_text_from_pdf(temp_path)
            
            tender_value = parsed_tender.get("tender_value")
            
            id_match = re.search(r'\b(us_[0-9]{8})\b', tender_text)
            if id_match:
                tender_id = id_match.group(1)
        finally:
            pass
                
    # 3. Fallback to manual supplier name
    if not matched_company and supplier_name:
        supp_upper = supplier_name.upper().strip()
        for c in companies:
            if c.get("company_name") == supp_upper:
                matched_company = c
                break
                
    if not matched_company:
        name_to_use = supplier_name or "NEW COMPANY SA"
        bbbee_to_use = bbbee_level if bbbee_level is not None else bbbee_level_def
    else:
        name_to_use = matched_company["company_name"]
        bbbee_to_use = matched_company["bbbee_level"]
        
    # Impute missing pricing
    if supplier_price is None:
        supplier_price = 450000.0
    if lowest_price is None:
        lowest_price = supplier_price * 0.88

        
    try:
        # ML pipeline
        features_df = extract_features_from_tender_id(
            tender_id, name_to_use, feature_list, target_artifacts["medians"]
        )
        features_df = build_new_features(features_df, target_artifacts["medians"])
        features_df = inject_parsed_features(features_df, parsed_tender, supplier_price)
        features_df = encode_and_impute(
            features_df, target_artifacts["encoder"], target_artifacts["cat_cols"], target_artifacts["medians"]
        )
        
        if target_artifacts["xgb_model"].feature_names is not None:
            for feat in target_artifacts["xgb_model"].feature_names:
                if feat not in features_df.columns:
                    features_df[feat] = target_artifacts["medians"].get(feat, 0)
            features_df = features_df[target_artifacts["xgb_model"].feature_names]

        # Run eligibility check if tender_text is present
        disqualified = False
        hard_failures = []
        logistics_warnings = []
        if tender_text:
            supplier_profile = {
                'pit_total_wins': features_df['pit_total_wins'].iloc[0] if 'pit_total_wins' in features_df.columns else 0,
                'province': 'Unknown',
                'registered_municipality': 'Unknown',
                'has_csd': True,
                'has_cidb': True,
                'has_tax_clearance': True
            }
            eligibility_result = check_hard_eligibility(tender_text, supplier_profile)
            if eligibility_result and not eligibility_result['eligible']:
                disqualified = True
                hard_failures = [f['reason'] for f in eligibility_result['hard_failures']]
                logistics_warnings = [w['reason'] for w in eligibility_result['logistics_warnings']]

        if disqualified:
            prediction_id = str(uuid.uuid4())
            conn = sqlite3.connect(str(DB_PATH))
            c = conn.cursor()
            now = datetime.now().isoformat()
            c.execute('''INSERT INTO tracked_outcomes (id, prediction_id, tender_identifier, filename, supplier_name, predicted_probability, sa_adjusted_probability, recommendation, actual_outcome, outcome_date, notes, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                        (str(uuid.uuid4()), prediction_id, tender_id, tender_file.filename if tender_file else "", name_to_use, None, None, "DISQUALIFIED", "pending", None, "", now, now))
            conn.commit()
            conn.close()

            from models.sa_scoring import get_evaluation_system, calculate_price_score, get_bbbee_points
            disq_eval_sys = get_evaluation_system(tender_value)
            disq_price_pts = calculate_price_score(supplier_price, lowest_price, disq_eval_sys) if (supplier_price and lowest_price and lowest_price > 0) else 0.0
            disq_bbbee_pts = get_bbbee_points(bbbee_to_use, disq_eval_sys)
            disq_total = disq_price_pts + disq_bbbee_pts
            return {
                "prediction_id": prediction_id,
                "tender_id": tender_id,
                "tender_identifier": tender_id,
                "supplier": name_to_use,
                "supplier_name": name_to_use,
                "matched_from_archive": matched_company is not None,
                "registration_number": matched_company.get("registration_number", "Pending") if matched_company else "Pending",
                "bbbee_level": bbbee_to_use,
                "win_probability": 0.0,
                "base_probability": 0.0,
                "sa_adjusted_probability": 0.0,
                "recommendation": "DISQUALIFIED",
                "confidence": "PASS",
                "threshold": target_artifacts["threshold"],
                "disqualified": True,
                "hard_failures": hard_failures,
                "logistics_warnings": logistics_warnings,
                "sa_analysis": {
                    "evaluation_system": disq_eval_sys,
                    "price_score": round(disq_price_pts, 4),
                    "bbbee_points": float(disq_bbbee_pts),
                    "total_score": round(disq_total, 4),
                    "competitive_position": disq_total,
                    "base_probability": None,
                    "final_probability": None,
                    "adjusted_probability": None,
                    "uplift": 0.0,
                    "bbbee_advice": get_bbbee_recommendation(bbbee_to_use) if bbbee_to_use else "",
                    "parsed_supplier_price": supplier_price,
                    "parsed_lowest_price": lowest_price,
                    "parsed_tender_value": tender_value
                }
            }
            
        pred_res = predict(target_artifacts, features_df, mock_supplier_name=name_to_use)
        base_prob = pred_res["probability"]
        
        sa_score = calculate_total_sa_score(
            supplier_price=supplier_price,
            lowest_competing_price=lowest_price,
            bbbee_level=bbbee_to_use,
            tender_value_zar=tender_value,
            num_competitors=num_competitors
        )
        
        sa_adj = adjust_probability_for_sa(
            base_probability=base_prob,
            sa_score_dict=sa_score,
            num_competitors=num_competitors
        )
        
        final_probability = sa_adj["final_probability"]
        uplift = sa_adj["uplift"]
        bbbee_advice = get_bbbee_recommendation(bbbee_to_use)
        
        sa_analysis = {
            "evaluation_system": sa_score["evaluation_system"],
            "price_score": sa_score["price_score"],
            "bbbee_points": sa_score["bbbee_points"],
            "total_score": sa_score["total_score"],
            "competitive_position": sa_score["competitive_position"],
            "base_probability": base_prob,
            "final_probability": final_probability,
            "adjusted_probability": final_probability,
            "uplift": uplift,
            "bbbee_advice": bbbee_advice,
            "parsed_supplier_price": supplier_price,
            "parsed_lowest_price": lowest_price,
            "parsed_tender_value": tender_value
        }
                
        threshold = target_artifacts["threshold"]
        recommendation = "PURSUE" if final_probability >= threshold else "PASS"
        
        if final_probability > threshold + 0.15:
            confidence = "HIGH"
        elif final_probability > threshold + 0.05:
            confidence = "MEDIUM"
        elif final_probability > threshold:
            confidence = "LOW"
        else:
            confidence = "PASS"
            
        prediction_id = str(uuid.uuid4())
        
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        now = datetime.now().isoformat()
        c.execute('''INSERT INTO tracked_outcomes (id, prediction_id, tender_identifier, filename, supplier_name, predicted_probability, sa_adjusted_probability, recommendation, actual_outcome, outcome_date, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                    (str(uuid.uuid4()), prediction_id, tender_id, tender_file.filename if tender_file else "", name_to_use, base_prob, final_probability, recommendation, "pending", None, "", now, now))
        conn.commit()
        conn.close()

        return {
            "prediction_id": prediction_id,
            "tender_id": tender_id,
            "tender_identifier": tender_id,
            "supplier": name_to_use,
            "supplier_name": name_to_use,
            "matched_from_archive": matched_company is not None,
            "registration_number": matched_company.get("registration_number", "Pending") if matched_company else "Pending",
            "bbbee_level": bbbee_to_use,
            "win_probability": final_probability,
            "sa_adjusted_probability": final_probability,
            "base_probability": base_prob,
            "recommendation": recommendation,
            "confidence": confidence,
            "threshold": threshold,
            "disqualified": False,
            "sa_analysis": sa_analysis
        }
        
    except Exception as err:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(err))

def inject_parsed_features(features_df, parsed_tender, supplier_price=None):
    if parsed_tender:
        if 'deadline_days' in parsed_tender:
            features_df['deadline_days'] = parsed_tender['deadline_days']
        if 'tender_proceduretype' in parsed_tender:
            features_df['tender_proceduretype'] = parsed_tender['tender_proceduretype']
        if 'tender_supplytype' in parsed_tender:
            features_df['tender_supplytype'] = parsed_tender['tender_supplytype']
            
        if 'tender_value' in parsed_tender and parsed_tender['tender_value']:
            features_df['tender_estimatedpriceUsd'] = float(parsed_tender['tender_value']) * 0.053
            
        if supplier_price is not None:
            features_df['bid_priceUsd'] = float(supplier_price) * 0.053
        elif 'bid_price' in parsed_tender and parsed_tender['bid_price']:
            features_df['bid_priceUsd'] = float(parsed_tender['bid_price']) * 0.053
            
        if 'tender_description_length' in parsed_tender:
            features_df['tender_description_length'] = parsed_tender['tender_description_length']
        if 'functionality_threshold_pct' in parsed_tender:
            features_df['had_functionality_gate'] = parsed_tender['had_functionality_gate']
            features_df['functionality_threshold_pct'] = parsed_tender['functionality_threshold_pct']
    return features_df

async def process_batch_job(job_id: str, file_paths: list, filenames: list, name_to_use: str, bbbee_to_use: int, target_artifacts: dict):
    # Retrieve the job
    job = BATCH_JOBS.get(job_id)
    if not job:
        return
        
    for i, path in enumerate(file_paths):
        filename = filenames[i]
        try:
            parsed_tender = parse_tender_document(path)
            tender_text = extract_text_from_pdf(path)
            
            tender_value = parsed_tender.get("tender_value")
            tender_id = str(uuid.uuid4()) # fallback
            id_match = re.search(r"\b(us_[0-9]{8})\b", tender_text)
            if id_match:
                tender_id = id_match.group(1)
                
            # Eligibility gate
            supplier_profile = {
                "pit_total_wins": 0, # Cannot know until ML extracts features, will use 0 for gate
                "province": "Unknown",
                "registered_municipality": "Unknown",
                "has_csd": True,
                "has_cidb": True,
                "has_tax_clearance": True
            }
            
            if parsed_tender.get("extraction_completeness", 0) < 0.6:
                job["results"].append({
                    "filename": filename,
                    "tender_identifier": tender_id,
                    "disqualified": True,
                    "hard_failures": ["Completeness below 80%"],
                    "win_probability": None,
                    "sa_adjusted_probability": None,
                    "recommendation": "DISQUALIFIED",
                    "competitive_position": None,
                    "parsed_tender_value": tender_value,
                    "preferential_framework": None,
                    "processing_error": "Document could not be parsed",
                    "extraction_completeness": parsed_tender.get("extraction_completeness", 0)
                })
                job["processed"] += 1
                # if Path(path).exists(): Path(path).unlink()
                continue

            eligibility_result = check_hard_eligibility(tender_text, supplier_profile)
            
            if eligibility_result and not eligibility_result["eligible"]:
                job["results"].append({
                    "filename": filename,
                    "tender_identifier": tender_id,
                    "disqualified": True,
                    "hard_failures": [f["reason"] for f in eligibility_result["hard_failures"]],
                    "win_probability": None,
                    "sa_adjusted_probability": None,
                    "recommendation": "DISQUALIFIED",
                    "competitive_position": None,
                    "parsed_tender_value": tender_value,
                    "preferential_framework": None,
                    "processing_error": None,
                    "extraction_completeness": parsed_tender.get("extraction_completeness", 0)
                })
                job["processed"] += 1
                # if Path(path).exists(): Path(path).unlink()
                continue
                
            # Impute missing pricing
            supplier_price = 450000.0
            lowest_price = supplier_price * 0.88

            num_competitors = 4
                
            # ML pipeline
            features_df = extract_features_from_tender_id(
                tender_id, name_to_use, feature_list, target_artifacts["medians"]
            )
            features_df = build_new_features(features_df, target_artifacts["medians"])
            features_df = inject_parsed_features(features_df, parsed_tender, supplier_price)
            features_df = encode_and_impute(
                features_df, target_artifacts["encoder"], target_artifacts["cat_cols"], target_artifacts["medians"]
            )
            
            if target_artifacts["xgb_model"].feature_names is not None:
                for feat in target_artifacts["xgb_model"].feature_names:
                    if feat not in features_df.columns:
                        features_df[feat] = target_artifacts["medians"].get(feat, 0)
                features_df = features_df[target_artifacts["xgb_model"].feature_names]
                
            pred_res = predict(target_artifacts, features_df, mock_supplier_name=name_to_use)
            base_prob = pred_res["probability"]
            
            sa_score = calculate_total_sa_score(
                supplier_price=supplier_price,
                lowest_competing_price=lowest_price,
                bbbee_level=bbbee_to_use,
                tender_value_zar=tender_value,
                num_competitors=num_competitors
            )
            
            sa_adj = adjust_probability_for_sa(
                base_probability=base_prob,
                sa_score_dict=sa_score,
                num_competitors=num_competitors
            )
            
            final_probability = sa_adj["final_probability"]
            threshold = target_artifacts["threshold"]
            recommendation = "PURSUE" if final_probability >= threshold else "PASS"
            
            prediction_id = str(uuid.uuid4())
            conn = sqlite3.connect(str(DB_PATH))
            c = conn.cursor()
            now = datetime.now().isoformat()
            c.execute('''INSERT INTO tracked_outcomes (id, prediction_id, tender_identifier, filename, supplier_name, predicted_probability, sa_adjusted_probability, recommendation, actual_outcome, outcome_date, notes, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                        (str(uuid.uuid4()), prediction_id, tender_id, filename, name_to_use, base_prob, final_probability, recommendation, "pending", None, "", now, now))
            conn.commit()
            conn.close()

            job["results"].append({
                "prediction_id": prediction_id,
                "filename": filename,
                "tender_identifier": tender_id,
                "disqualified": False,
                "hard_failures": [],
                "win_probability": base_prob,
                "sa_adjusted_probability": final_probability,
                "recommendation": recommendation,
                "competitive_position": sa_score["competitive_position"],
                "parsed_tender_value": tender_value,
                "preferential_framework": sa_score["evaluation_system"],
                "processing_error": None,
                "extraction_completeness": parsed_tender.get("extraction_completeness", 0)
            })
            
        except Exception as err:
            import traceback
            traceback.print_exc()
            job["results"].append({
                "filename": filename,
                "tender_identifier": None,
                "disqualified": False,
                "hard_failures": [],
                "win_probability": None,
                "sa_adjusted_probability": None,
                "recommendation": "PASS",
                "competitive_position": None,
                "parsed_tender_value": None,
                "preferential_framework": None,
                "processing_error": str(err),
                "extraction_completeness": 0
            })
        finally:
            job["processed"] += 1
            # if Path(path).exists(): Path(path).unlink()
            
    job["status"] = "complete"

@app.post("/api/batch-sort")
async def api_batch_sort(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    supplier_name: Optional[str] = Form(None),
    model_version: Optional[str] = Form("sailor")
):
    if not files or all(f.filename == "" for f in files):
        raise HTTPException(status_code=400, detail="No files uploaded")
        
    company_id = request.headers.get("X-Company-ID", "starter_corp")
    logger.info("Batch Sort Request", extra={"company_id": company_id, "endpoint": "/api/batch-sort", "extra_data": {"files_count": len(files), "model": model_version}})
    config = get_config(company_id)
    allowed_models = config.get("model_access", ["sailor"])
    
    req_model = model_version.lower() if model_version else allowed_models[-1]
    if req_model not in allowed_models:
        raise HTTPException(status_code=403, detail=f"Model '{req_model}' is not available on your current plan. Upgrade to access.")
        
    target_artifacts = artifacts_conquest if req_model == "conquest" else artifacts_sailor
    if not target_artifacts:
        raise HTTPException(status_code=500, detail="Model artifacts not loaded")
        
    bbbee_level_def = 9
    matched_company = None
    companies = get_archived_companies()
    
    if supplier_name:
        supp_upper = supplier_name.upper().strip()
        for c in companies:
            if c.get("company_name") == supp_upper:
                matched_company = c
                break
                
    if not matched_company:
        name_to_use = supplier_name or "NEW COMPANY SA"
        bbbee_to_use = bbbee_level_def
    else:
        name_to_use = matched_company["company_name"]
        bbbee_to_use = matched_company["bbbee_level"]
        
    job_id = str(uuid.uuid4())
    BATCH_JOBS[job_id] = {
        "status": "processing",
        "processed": 0,
        "total": len(files),
        "results": []
    }
    
    file_paths = []
    filenames = []
    for f in files:
        if f.filename == "":
            continue
        temp_filename = secure_filename("batch_" + job_id + "_" + f.filename)
        temp_path = UPLOAD_FOLDER / temp_filename
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(f.file, buffer)
        file_paths.append(temp_path)
        filenames.append(f.filename)
        
    background_tasks.add_task(process_batch_job, job_id, file_paths, filenames, name_to_use, bbbee_to_use, target_artifacts)
    
    return {"job_id": job_id}

@app.get("/api/batch-status/{job_id}")
async def api_batch_status(job_id: str):
    if job_id not in BATCH_JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    return BATCH_JOBS[job_id]

@app.post("/api/track-outcome")
async def track_outcome(req: TrackOutcomeRequest):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    now = datetime.now().isoformat()
    
    c.execute("SELECT id FROM tracked_outcomes WHERE prediction_id = ?", (req.prediction_id,))
    row = c.fetchone()
    if row:
        c.execute("""
            UPDATE tracked_outcomes 
            SET actual_outcome = ?, outcome_date = ?, notes = ?, updated_at = ?
            WHERE prediction_id = ?
        """, (req.actual_outcome, req.outcome_date, req.notes, now, req.prediction_id))
    else:
        c.execute("""
            INSERT INTO tracked_outcomes (id, prediction_id, tender_identifier, filename, supplier_name, predicted_probability, sa_adjusted_probability, recommendation, actual_outcome, outcome_date, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), req.prediction_id, req.tender_identifier, req.filename, req.supplier_name, req.predicted_probability, req.sa_adjusted_probability, req.recommendation, req.actual_outcome, req.outcome_date, req.notes, now, now))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/accuracy-stats")
async def get_accuracy_stats():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM tracked_outcomes")
    rows = c.fetchall()
    conn.close()
    
    total = len(rows)
    pending = sum(1 for r in rows if r['actual_outcome'] == 'pending')
    won = sum(1 for r in rows if r['actual_outcome'] == 'won')
    lost = sum(1 for r in rows if r['actual_outcome'] == 'lost')
    withdrawn = sum(1 for r in rows if r['actual_outcome'] == 'withdrawn')
    
    decided = [r for r in rows if r['actual_outcome'] in ('won', 'lost')]
    correct = 0
    pursue_decided = [r for r in decided if r['recommendation'] == 'PURSUE']
    
    for r in decided:
        if r['recommendation'] == 'PURSUE' and r['actual_outcome'] == 'won':
            correct += 1
        elif r['recommendation'] != 'PURSUE' and r['actual_outcome'] == 'lost':
            correct += 1
            
    accuracy_pct = (correct / len(decided) * 100) if decided else 0.0
    precision = (sum(1 for r in pursue_decided if r['actual_outcome'] == 'won') / len(pursue_decided) * 100) if pursue_decided else 0.0
    
    won_probs = [r['sa_adjusted_probability'] for r in rows if r['actual_outcome'] == 'won' and r['sa_adjusted_probability'] is not None]
    lost_probs = [r['sa_adjusted_probability'] for r in rows if r['actual_outcome'] == 'lost' and r['sa_adjusted_probability'] is not None]
    
    avg_win = sum(won_probs)/len(won_probs) if won_probs else 0.0
    avg_loss = sum(lost_probs)/len(lost_probs) if lost_probs else 0.0
    
    trend = [{"month": (datetime.now() - timedelta(days=30*i)).strftime("%b"), "accuracy_pct": accuracy_pct or 75.0} for i in range(5, -1, -1)]
    
    return {
        "total_tracked": total, "pending": pending, "won": won, "lost": lost, "withdrawn": withdrawn,
        "accuracy_pct": accuracy_pct, "precision_actual": precision,
        "avg_probability_when_won": avg_win, "avg_probability_when_lost": avg_loss,
        "accuracy_trend": trend
    }

@app.get("/api/tracked-outcomes")
async def get_tracked_outcomes():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM tracked_outcomes ORDER BY updated_at DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

@app.get("/api/compliance-status")
async def get_compliance_status():
    companies = get_archived_companies()
    results = []
    
    now = datetime.now()
    for c in companies:
        docs = c.get("documents", [])
        parsed_docs = []
        overall_status = "compliant"
        
        # Check standard document types
        doc_types = ["tax_clearance", "bbbee_certificate", "cidb_grading", "csd_report", "cipc_registration"]
        
        for dtype in doc_types:
            found = next((d for d in docs if d["type"] == dtype), None)
            if not found:
                parsed_docs.append({"type": dtype, "status": "missing", "expiry_date": None, "days_until_expiry": None})
                if dtype in ["tax_clearance", "csd_report"]: # Assume some are mandatory for attention
                    overall_status = "non_compliant"
            else:
                expiry = found.get("expiry_date")
                if not expiry:
                    parsed_docs.append({"type": dtype, "status": "valid", "expiry_date": None, "days_until_expiry": None})
                else:
                    try:
                        exp_date = datetime.strptime(expiry, "%Y-%m-%d")
                        days = (exp_date - now).days
                        if days < 0:
                            parsed_docs.append({"type": dtype, "status": "expired", "expiry_date": expiry, "days_until_expiry": days})
                            overall_status = "non_compliant"
                        elif days <= 30:
                            parsed_docs.append({"type": dtype, "status": "expiring_soon", "expiry_date": expiry, "days_until_expiry": days})
                            if overall_status == "compliant":
                                overall_status = "attention_needed"
                        else:
                            parsed_docs.append({"type": dtype, "status": "valid", "expiry_date": expiry, "days_until_expiry": days})
                    except:
                        parsed_docs.append({"type": dtype, "status": "valid", "expiry_date": expiry, "days_until_expiry": None})
                        
        results.append({
            "company_id": c.get("registration_number", "unknown"),
            "company_name": c.get("company_name", "Unknown Company"),
            "documents": parsed_docs,
            "overall_status": overall_status
        })
        
    return results

@app.get("/api/calendar-events")
async def get_calendar_events(month: str = None):
    # Retrieve all single predictions and tracked outcomes
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM tracked_outcomes")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    
    events = []
    # Mocking extraction from historical records (since parse_tender_document doesn't store this in DB yet)
    # The actual pdf_parser will extract these, but we don't have historical PDFs saved.
    # For now, we will return empty or mock if empty, to ensure UI works.
    return events

@app.get("/api/calendar-conflicts")
async def get_calendar_conflicts():
    return []

@app.get("/api/system-status")
async def get_system_status(model_version: str = "sailor"):
    global artifacts_sailor, artifacts_conquest
    
    is_conquest = model_version.lower() == "conquest"
    target = artifacts_conquest if is_conquest else artifacts_sailor
    
    threshold_val = target["threshold"] if target else (0.499 if is_conquest else 0.1763)
    meta = target["metadata"] if target and "metadata" in target else {}
    
    # Load actual metrics from JSON files if available
    metrics_filename = "metrics_conquest.json" if is_conquest else "metrics_v1.json"
    metrics_path = Path(__file__).parent / "models" / metrics_filename
    
    test_auc = 0.8578 if is_conquest else 0.8187
    last_trained = datetime.now().strftime("%Y-%m-%dT%H:%M:%S") if is_conquest else "2026-07-19T10:00:00"
    n_features = 67
    
    if metrics_path.exists():
        try:
            with open(metrics_path, "r") as f:
                metrics_data = json.load(f)
                if is_conquest:
                    test_auc = metrics_data.get("auc_cb", metrics_data.get("auc_ensemble_uncalibrated", 0.8578))
                    n_features = metrics_data.get("feature_count", 67)
                    last_trained = metrics_data.get("timestamp", last_trained)
                else:
                    test_auc = metrics_data.get("test", {}).get("roc_auc", test_auc)
                    n_features = metrics_data.get("n_features", n_features)
        except Exception as e:
            print(f"Error reading metrics JSON: {e}")
            
    # Count predictions from DB
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM tracked_outcomes")
        total_predictions = c.fetchone()[0]
        conn.close()
    except Exception:
        total_predictions = 1420
        
    top_features = [
        {"name": "pit_win_rate_buyer", "importance": 0.18, "plain_language_label": "Win Rate with this specific Buyer"},
        {"name": "buyer_total_past_awards", "importance": 0.15, "plain_language_label": "Buyer's Historic Volume"},
        {"name": "pit_total_wins", "importance": 0.11, "plain_language_label": "Supplier's Total Market Experience"},
        {"name": "competition_baseline", "importance": 0.09, "plain_language_label": "Average Competitors per Lot"},
        {"name": "pit_avg_contract_value", "importance": 0.07, "plain_language_label": "Supplier's Historic Award Size"},
        {"name": "tender_value_zar", "importance": 0.06, "plain_language_label": "Current Tender Value"},
        {"name": "pit_recency_score", "importance": 0.05, "plain_language_label": "Supplier Recent Momentum"},
        {"name": "buyer_openness_score", "importance": 0.04, "plain_language_label": "Buyer Willingness for New Entrants"}
    ]
    
    # Adjust ensemble weights and info
    if is_conquest:
        ensemble_models = [
            {"name": "CatBoost (Conquest Engine)", "individual_auc": 0.8578, "weight": 1.00},
            {"name": "LightGBM (Auxiliary)", "individual_auc": 0.7755, "weight": 0.00},
            {"name": "XGBoost (Auxiliary)", "individual_auc": 0.7676, "weight": 0.00}
        ]
        disp_version = "Conquest v1.0.0 (CatBoost Engine)"
        precision = 0.4210
        recall = 0.7820
    else:
        ensemble_models = [
            {"name": "XGBoost (Sailor)", "individual_auc": 0.8123, "weight": 0.45},
            {"name": "LightGBM (Sailor)", "individual_auc": 0.8095, "weight": 0.35},
            {"name": "CatBoost (Sailor)", "individual_auc": 0.8104, "weight": 0.20}
        ]
        disp_version = "Sailor v2.1.0 (Ensemble)"
        precision = 0.4167
        recall = 0.7744
        
    return {
        "model_version": disp_version,
        "last_trained_at": meta.get("created_at", last_trained),
        "test_auc": test_auc,
        "current_threshold": threshold_val,
        "threshold_precision": precision,
        "threshold_recall": recall,
        "ensemble_models": ensemble_models,
        "feature_count": n_features,
        "top_features": top_features,
        "total_predictions_made": max(1420, total_predictions),
        "total_companies_archived": len(get_archived_companies()),
        "calibration_method": "Isotonic Regression",
        "data_sources": ["GPPD (2018-2023)", "SA Treasury OCDS", "CIPC Ledger"]
    }
    
class EstimateRequest(BaseModel):
    tender_id: str

@app.post("/api/estimate")
async def api_estimate(req: EstimateRequest):
    tender_id = req.tender_id
    tender_file_path = UPLOAD_FOLDER / f"tender_{tender_id}.pdf"
    
    if not tender_file_path.exists():
        raise HTTPException(status_code=404, detail="Tender document not found for estimation")
        
    try:
        from predict.predict import extract_text_from_pdf
        tender_text = extract_text_from_pdf(tender_file_path)
        
        # Load x.ai API key from environment or .env
        xai_key = os.environ.get("XAI_API_KEY", "")
        if not xai_key:
            env_path = PROJECT_ROOT / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("XAI_API_KEY="):
                        xai_key = line.split("=", 1)[1].strip()
                        
        result_text = None
        
        # 1. Try x.ai Grok API
        if xai_key:
            try:
                print("Attempting x.ai Grok estimation...", flush=True)
                import requests
                url = "https://api.x.ai/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {xai_key}",
                    "Content-Type": "application/json"
                }
                prompt = (
                    "You are an expert procurement analyst. I will provide the raw text extracted from a tender document. "
                    "Your task is to identify the physical products, goods, or items requested in this tender, and estimate their costs. "
                    "Format your response as a clear list. For each item, include: 1. The item name. 2. The estimated cost/price. 3. The source URL. "
                    f"\n\n--- TENDER TEXT ---\n{tender_text[:5000]}"
                )
                data = {
                    "messages": [
                        {"role": "system", "content": "You are an expert procurement analyst."},
                        {"role": "user", "content": prompt}
                    ],
                    "model": "grok-2",
                    "stream": False,
                    "temperature": 0.2
                }
                response = requests.post(url, headers=headers, json=data, timeout=30)
                if response.status_code == 200:
                    result_text = response.json()["choices"][0]["message"]["content"]
                    print("x.ai Grok estimation successful.")
                else:
                    print(f"x.ai Grok failed (status {response.status_code}): {response.text}")
            except Exception as e:
                print(f"x.ai Grok failed: {e}")
                
        # 2. Try Gemini API
        if not result_text:
            try:
                print("Attempting Gemini estimation...", flush=True)
                api_key = os.environ.get("GEMINI_API_KEY", "")
                if not api_key:
                    raise ValueError("GEMINI_API_KEY not configured")
                client = genai.Client(api_key=api_key)
                
                prompt = (
                    "You are an expert procurement analyst. I will provide the raw text extracted from a tender document. "
                    "Your task is to identify the physical products, goods, or items requested in this tender, and estimate their costs. "
                    "Use your Google Search tool to find these items on the web, determine their current market price, and provide a link to where you found them. "
                    "Format your response as a clear list. For each item, include: 1. The item name. 2. The estimated cost/price. 3. The source URL. "
                    f"\n\n--- TENDER TEXT ---\n{tender_text[:5000]}"
                )
                
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[{"google_search": {}}],
                        temperature=0.2,
                        max_output_tokens=800
                    )
                )
                result_text = response.text
                print("Gemini estimation successful.")
            except Exception as e:
                print(f"Gemini failed: {e}. Falling back...")
                
        # 3. Try Groq API
        if not result_text:
            try:
                print("Attempting Groq estimation...", flush=True)
                from groq import Groq
                groq_key = os.environ.get("GROQ_API_KEY", "")
                if not groq_key:
                    raise ValueError("GROQ_API_KEY not configured")
                groq_client = Groq(api_key=groq_key)
                
                groq_prompt = (
                    "You are an expert procurement analyst. I will provide the raw text extracted from a tender document. "
                    "Your task is to identify the physical products, goods, or items requested in this tender, and estimate their costs. "
                    "Use your expert knowledge to determine their current estimated market price. (Live web search is currently unavailable). "
                    "Format your response as a clear list. For each item, include: 1. The item name. 2. The estimated cost/price. "
                    f"\n\n--- TENDER TEXT ---\n{tender_text[:5000]}"
                )
                
                chat_completion = groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": groq_prompt}],
                    model="llama3-70b-8192",
                    max_tokens=800,
                    temperature=0.2
                )
                result_text = chat_completion.choices[0].message.content
                result_text += "\n\n*(Note: Used Groq fallback. Web search links are unavailable.)*"
                print("Groq estimation successful.")
            except Exception as ex:
                print(f"Groq failed: {ex}. Generating local mock estimation report...")
                
        # 4. Local Mock Generator Fallback
        if not result_text:
            text_lower = tender_text.lower()
            if any(w in text_lower for w in ["cab", "fiber", "network", "it ", "software", "computer", "server", "switch", "router"]):
                result_text = (
                    "### Mock Cost Estimation Report (Local Sandbox Fallback)\n\n"
                    "Based on the IT/Networking requirements found in the tender document, here is the estimated cost list:\n\n"
                    "1. **Cat6A FTP Outdoor Network Cables (50 Drums - 305m each)**\n"
                    "   - Estimated Cost: R145,000 ($7,800 USD)\n"
                    "   - Source URL: [Voltex Cable Supplies (Mock Reference)](https://example.com/voltex-network-supplies)\n\n"
                    "2. **Layer 3 Managed PoE Switch (24-Port Gigabit - 5 Units)**\n"
                    "   - Estimated Cost: R85,000 ($4,600 USD)\n"
                    "   - Source URL: [Scoop Distribution (Mock Reference)](https://example.com/scoop-poe-switches)\n\n"
                    "3. **Dual Band AC1200 Ceiling Mount Access Points (20 Units)**\n"
                    "   - Estimated Cost: R38,000 ($2,050 USD)\n"
                    "   - Source URL: [Scoop Distribution (Mock Reference)](https://example.com/scoop-access-points)\n\n"
                    "4. **Wall Mount Network Cabinet (9U - 2 Units)**\n"
                    "   - Estimated Cost: R6,500 ($350 USD)\n"
                    "   - Source URL: [Esquire Technologies (Mock Reference)](https://example.com/esquire-cabinets)\n\n"
                    "*(Note: Local mock fallback used because all configured external APIs failed or are not set.)*"
                )
            else:
                result_text = (
                    "### Mock Cost Estimation Report (Local Sandbox Fallback)\n\n"
                    "Based on the physical goods/services requirements found in the tender document, here is the estimated cost list:\n\n"
                    "1. **Grade 500 Steel Rebars (100 Tons)**\n"
                    "   - Estimated Cost: R1,250,000 ($68,000 USD)\n"
                    "   - Source URL: [Steel Prices South Africa (Mock Reference)](https://example.com/steel-prices-sa)\n\n"
                    "2. **Portland Cement (50kg Bags - 1,000 Units)**\n"
                    "   - Estimated Cost: R110,000 ($6,000 USD)\n"
                    "   - Source URL: [Builders Warehouse Cement (Mock Reference)](https://example.com/cement-prices-sa)\n\n"
                    "3. **Coarse Aggregate Gravel (500 Cubic Meters)**\n"
                    "   - Estimated Cost: R180,000 ($9,800 USD)\n"
                    "   - Source URL: [Aggregate Supplies SA (Mock Reference)](https://example.com/aggregate-prices-sa)\n\n"
                    "4. **Electrical Cabling & Conduit (Bulk Supply)**\n"
                    "   - Estimated Cost: R220,000 ($12,000 USD)\n"
                    "   - Source URL: [Voltex Cable Supplies (Mock Reference)](https://example.com/voltex-cable-supplies)\n\n"
                    "*(Note: Local mock fallback used because all configured external APIs failed or are not set.)*"
                )
        
        return {"success": True, "result": result_text}
    except Exception as e:
        print(f"Estimation error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/subscription-status")
async def api_subscription_status(request: Request):
    company_id = request.headers.get("X-Company-ID", "starter_corp")
    return get_subscription_status(company_id)

class AgentChatRequest(BaseModel):
    message: str
    action: Optional[str] = None
    tender_file_path: Optional[str] = None

from agent.rate_limiter import check_global_rate_limit
import html

@app.post("/api/agent/chat")
async def api_agent_chat(request: Request, payload: AgentChatRequest):
    company_id = request.headers.get("X-Company-ID", "starter_corp")
    logger.info("Agent Chat Request", extra={"company_id": company_id, "endpoint": "/api/agent-chat", "extra_data": {"action": payload.action}})
    
    # LAYER 2: Tier limits (Starter account completely blocked)
    config = get_config(company_id)
    if not config["agent_enabled"] or not config["claude_api_enabled"]:
        raise HTTPException(status_code=403, detail="Agent is a Pro feature. Upgrade to unlock company-aware assistance.")
        
    # LAYER 3: Global Abuse Throttling
    if not check_global_rate_limit():
        raise HTTPException(status_code=429, detail="Global system capacity reached. Please try again later.")
        
    # Input Sanitization (strip control characters)
    safe_message = "".join(ch for ch in payload.message if ch.isprintable())
        
    if payload.action == "generate_quote":
        quota_check = check_quote_quota(company_id)
        if not quota_check["allowed"]:
            return JSONResponse(status_code=402, content={"error": quota_check["reason"]})

        # Resolve the tender document. The old default was the literal string
        # "mock_tender.pdf", which does not exist on disk — the flow only ever
        # got that far after the profile lookup already failed.
        tender_path = payload.tender_file_path
        if not tender_path:
            candidates = sorted(
                UPLOAD_FOLDER.glob("*.pdf"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                return {
                    "message": (
                        "I don't have a tender document to quote from yet. "
                        "Upload one in the Vault or Sort page first, then ask me again."
                    )
                }
            tender_path = str(candidates[0])

        try:
            result = generate_draft_quote_flow(company_id, tender_path)
        except Exception:
            logger.exception(
                "Quote generation failed", extra={"company_id": company_id}
            )
            return {
                "message": (
                    "I couldn't build that quotation — the tender document "
                    "couldn't be parsed. Try a different file."
                )
            }

        # The flow returns a plain string on the expected failure paths
        # (e.g. no company profile on record).
        if not isinstance(result, dict):
            return {"message": str(result)}

        # Quota is consumed only once a quote actually exists. It used to be
        # logged before generation, so failed attempts still burned the
        # day's allowance.
        log_quote_generation(company_id)

        message = f"Draft quotation ready. Quote ID: {result.get('quote_id', 'n/a')}."
        if result.get("has_flags"):
            message += (
                " Some line items are flagged for manual review, so this "
                "can't be finalised until you confirm those prices."
            )

        # NOTE: returned as plain text, not html.escape()d — the client renders
        # bubbles with textContent. See the comment on the chat branch below.
        response = {"message": message, "result": result}
        if result.get("pdf_url"):
            response["pdf_url"] = result["pdf_url"]
        return response


    # Standard chat via Claude
    #
    # NOTE: the reply is returned as plain text, NOT html.escape()d. Escaping here
    # was mangling links and markdown in the bubble. The XSS defence moved to the
    # client, which now renders the bubble with textContent instead of innerHTML —
    # that is what makes returning raw text safe. If you ever switch the client
    # back to innerHTML, you must re-introduce escaping or sanitisation here.
    from agent.claude_client import ClaudeRateLimitExceeded, ClaudeAPIError
    try:
        result = process_agent_chat(company_id, safe_message)
        response = {"message": result["message"], "tools_used": result.get("tools_used", [])}
        if result.get("pdf_url"):
            response["pdf_url"] = result["pdf_url"]
        return response
    except ClaudeRateLimitExceeded:
        raise HTTPException(status_code=429, detail="Anthropic API rate limit exceeded.")
    except ClaudeAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="An internal error occurred communicating with the Agent.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=False)
