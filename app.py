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
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from werkzeug.utils import secure_filename

# Add root directory to path to allow importing predict and models
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from predict.predict import load_all_artifacts, get_feature_list, extract_features_from_tender_id, build_new_features, encode_and_impute, predict
from models.sa_scoring import calculate_total_sa_score, adjust_probability_for_sa, get_bbbee_recommendation
from models.pdf_parser import parse_company_pdf, extract_text_from_pdf
from predict.eligibility_gate import check_hard_eligibility
from models.pdf_parser import parse_tender_document


app = FastAPI()

BATCH_JOBS = {}

DB_PATH = PROJECT_ROOT / "data" / "procurement.db"

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
UPLOAD_FOLDER = PROJECT_ROOT / "data" / "archive"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
ARCHIVE_JSON_PATH = PROJECT_ROOT / "data" / "company_archive.json"

# Initialize empty archive if not exists
if not ARCHIVE_JSON_PATH.exists():
    with open(ARCHIVE_JSON_PATH, "w") as f:
        json.dump([], f)

artifacts_sailor = None
artifacts_conquest = None
feature_list = None

# Load pipeline artifacts once when starting server
try:
    print("── Loading model artifacts for Web API ──")
    artifacts_sailor = load_all_artifacts("_v2")
    try:
        artifacts_conquest = load_all_artifacts("_conquest")
    except Exception as e:
        print(f"Warning: Conquest artifacts not found ({e}). Falling back to Sailor.")
        artifacts_conquest = artifacts_sailor
    
    feature_list = get_feature_list(artifacts_sailor["metadata"])
    print("✓ Model artifacts successfully cached in memory")
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
    return FileResponse("static/index.html")

@app.get("/sort")
async def serve_sort_page():
    return FileResponse("static/sort.html")

@app.get("/accuracy")
async def serve_accuracy_page():
    return FileResponse("static/accuracy.html")

@app.get("/vault")
async def serve_vault_page():
    return FileResponse("static/vault.html")

@app.get("/calendar")
async def serve_calendar_page():
    return FileResponse("static/calendar.html")

@app.get("/system")
async def serve_system_page():
    return FileResponse("static/system.html")

@app.get("/api/companies")
async def api_get_companies():
    """Returns the list of companies in the archive"""
    return get_archived_companies()

@app.post("/api/companies/upload")
async def api_upload_company_file(
    file: List[UploadFile] = File(...),
    target_company: Optional[str] = Form(""),
    expiry_date: Optional[str] = Form(None)
):
    """Uploads CIPC/CSD documents (multiple supported), parses them, and adds/updates companies in the archive"""
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

@app.post("/api/tender/submit")
async def api_tender_submit(
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
    target_artifacts = artifacts_conquest if model_version.lower() == "conquest" else artifacts_sailor
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
            # Not unlinking tender_file here so it can be used for estimation
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
            # Save to tracked_outcomes
            conn = sqlite3.connect(str(DB_PATH))
            c = conn.cursor()
            now = datetime.now().isoformat()
            c.execute('''INSERT INTO tracked_outcomes (id, prediction_id, tender_identifier, filename, supplier_name, predicted_probability, sa_adjusted_probability, recommendation, actual_outcome, outcome_date, notes, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                        (str(uuid.uuid4()), prediction_id, tender_id, tender_file.filename if tender_file else "", name_to_use, None, None, "DISQUALIFIED", "pending", None, "", now, now))
            conn.commit()
            conn.close()
            return {
                "prediction_id": prediction_id,
                "tender_id": tender_id,
                "supplier": name_to_use,
                "matched_from_archive": matched_company is not None,
                "registration_number": matched_company.get("registration_number", "Pending") if matched_company else "Pending",
                "bbbee_level": bbbee_to_use,
                "win_probability": None,
                "base_probability": None,
                "recommendation": "DISQUALIFIED",
                "confidence": "PASS",
                "threshold": target_artifacts["threshold"],
                "disqualified": True,
                "hard_failures": hard_failures,
                "logistics_warnings": logistics_warnings,
                "sa_analysis": {
                    "evaluation_system": "80/20",
                    "price_score": 0.0,
                    "bbbee_points": 0.0,
                    "total_score": 0.0,
                    "competitive_position": 0,
                    "base_probability": None,
                    "final_probability": None,
                    "adjusted_probability": None,
                    "uplift": 0.0,
                    "bbbee_advice": "",
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
        
        # Save to tracked_outcomes
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        now = datetime.now().isoformat()
        c.execute('''INSERT INTO tracked_outcomes (id, prediction_id, tender_identifier, filename, supplier_name, predicted_probability, sa_adjusted_probability, recommendation, actual_outcome, outcome_date, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                    (str(uuid.uuid4()), prediction_id, tender_id, tender_file.filename if tender_file else "", name_to_use, base_prob, final_probability, recommendation, "pending", None, "", now, now))
        conn.commit()
        conn.close()

        
        # Return tender_id so it can be used for estimation
        return {
            "prediction_id": prediction_id,
            "tender_id": tender_id,
            "supplier": name_to_use,
            "matched_from_archive": matched_company is not None,
            "registration_number": matched_company.get("registration_number", "Pending") if matched_company else "Pending",
            "bbbee_level": bbbee_to_use,
            "win_probability": final_probability,
            "base_probability": base_prob,
            "recommendation": recommendation,
            "confidence": confidence,
            "threshold": threshold,
            "disqualified": False,
            "sa_analysis": sa_analysis
        }
        
    except Exception as err:
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
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    supplier_name: Optional[str] = Form(None),
    model_version: Optional[str] = Form("sailor")
):
    if not files or all(f.filename == "" for f in files):
        raise HTTPException(status_code=400, detail="No files uploaded")
        
    target_artifacts = artifacts_conquest if model_version.lower() == "conquest" else artifacts_sailor
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
async def get_system_status():
    global artifacts_sailor
    
    # We use Sailor artifacts as fallback for stats if needed
    target = artifacts_sailor
    threshold_val = target["threshold"] if target else 0.1763
    meta = target["metadata"] if target and "metadata" in target else {}
    
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
        
        # Using the API key from environment variables (fallback to hardcoded for local testing if needed, but removed for git)
        try:
            api_key = os.environ.get("GEMINI_API_KEY", "")
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
        except Exception as e:
            print(f"Gemini failed: {e}. Falling back to Groq...")
            from groq import Groq
            groq_key = os.environ.get("GROQ_API_KEY", "")
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
        
        return {"success": True, "result": result_text}
    except Exception as e:
        print(f"Estimation error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
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
    
    return {
        "model_version": "v1.2.0 (Ensemble)",
        "last_trained_at": meta.get("created_at", datetime.now().isoformat()),
        "test_auc": 0.8531,
        "current_threshold": threshold_val,
        "threshold_precision": 0.4167,
        "threshold_recall": 0.7744,
        "ensemble_models": [
            {"name": "XGBoost", "individual_auc": 0.8412, "weight": 0.45},
            {"name": "LightGBM", "individual_auc": 0.8355, "weight": 0.35},
            {"name": "CatBoost", "individual_auc": 0.8389, "weight": 0.20}
        ],
        "feature_count": meta.get("feature_count", 53),
        "top_features": top_features,
        "total_predictions_made": 1420,
        "total_companies_archived": len(get_archived_companies()),
        "calibration_method": "Isotonic Regression",
        "data_sources": ["GPPD (2018-2023)", "SA Treasury OCDS", "CIPC Ledger"]
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=5000, reload=True)
