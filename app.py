from predict.regional_router import predict_tender_region, detect_region
import traceback

from predict import model_validation
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
from models.sa_scoring import (calculate_total_sa_score, adjust_probability_for_sa,
                               get_bbbee_recommendation, get_evaluation_system)
from models.pdf_parser import parse_company_pdf, extract_text_from_pdf, classify_document_type
from predict.eligibility_gate import check_hard_eligibility
from models.pdf_parser import parse_tender_document
# Vault uploads are recorded against the company here; the vault reads from the
# same table, which is what makes an uploaded document actually appear.
from agent.memory.company_store import add_company_document
from agent.memory import company_store
from agent.tool_dispatch import vault_type_for
from models.quotation_generator import generate_quotation_pdf

from agent.subscription import get_config, check_quote_quota, get_subscription_status, log_quote_generation, get_company_tier
from agent.main_agent import generate_draft_quote_flow, finalize_quote_flow, process_agent_chat, memory_tools, app_help_tools, onboarding_tools, quotation_tools

# Authentication. Until this existed, every company-aware route below read its
# tenant from `request.headers.get("X-Company-ID", "starter_corp")` — a header
# any caller can set, with a default for callers that do not bother. Anyone
# could be any company, and every isolation control in this codebase pinned to
# that asserted value. `require_company_id` resolves the tenant from a verified
# session cookie or device token instead, and has no default: an unauthenticated
# request is a 401, not somebody's data.
from agent.auth import Principal, require_company_id, require_principal
from fastapi import Depends

import logging
from logging.handlers import RotatingFileHandler

LOG_DIR = PROJECT_ROOT / "logs"
if os.environ.get("K_SERVICE"):
    LOG_DIR = Path("/tmp/logs")
os.makedirs(str(LOG_DIR), exist_ok=True)

#: Python level -> the strings Cloud Logging recognises. Without `severity` in
#: the payload every line is DEFAULT severity, so an alert policy cannot tell an
#: error from an info line and `severity>=ERROR` matches nothing.
_CLOUD_SEVERITY = {
    "DEBUG": "DEBUG", "INFO": "INFO", "WARNING": "WARNING",
    "ERROR": "ERROR", "CRITICAL": "CRITICAL",
}


class JSONFormatter(logging.Formatter):
    """
    One JSON object per line, shaped so Cloud Logging parses it.

    `severity` and `message` are the two field names it reads; everything else
    lands in jsonPayload and is queryable. The exception type is promoted to
    its own field because an alert that says "errors are up" is only actionable
    if you can group them by what broke.
    """

    def format(self, record):
        log_obj = {
            "timestamp": self.formatTime(record, self.datefmt),
            "severity": _CLOUD_SEVERITY.get(record.levelname, "DEFAULT"),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "funcName": record.funcName,
            "logger": record.name,
        }
        if hasattr(record, "company_id"):
            log_obj["company_id"] = record.company_id
        if hasattr(record, "endpoint"):
            log_obj["endpoint"] = record.endpoint
        if hasattr(record, "extra_data"):
            log_obj.update(record.extra_data)

        # The TypeError in the prediction path lived in production because
        # nothing carried it anywhere a person would look. A traceback in the
        # payload is what turns an alert into a diagnosis.
        if record.exc_info and record.exc_info[0] is not None:
            log_obj["error_type"] = record.exc_info[0].__name__
            log_obj["stack_trace"] = self.formatException(record.exc_info)

        return json.dumps(log_obj, default=str)


logger = logging.getLogger("api_monitor")
logger.setLevel(logging.INFO)
# Avoid adding multiple handlers in hot reloads
if not logger.handlers:
    # STDOUT FIRST, AND THIS IS THE ONE THAT MATTERS IN PRODUCTION.
    #
    # Until this there was only the file handler below, writing to LOG_DIR —
    # which is /tmp/logs on Cloud Run, per-instance and wiped on cold start.
    # Nothing reached Cloud Logging at all: Python's last-resort stderr handler
    # only fires for records no handler took, and the file handler took every
    # one. Verified by capturing stdout and stderr around logger.error() and
    # getting two empty strings.
    #
    # So the premise of "errors land in Cloud Logging and nobody is told" was
    # optimistic. They were not landing anywhere a person could reach, and no
    # alert policy could have fired on them.
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(JSONFormatter())
    logger.addHandler(stream_handler)

    # Kept for local development, where tailing a file is convenient. On Cloud
    # Run it writes to an ephemeral disk and is effectively a no-op.
    log_handler = RotatingFileHandler(str(LOG_DIR / "api.log"), maxBytes=5000000, backupCount=5)
    log_handler.setFormatter(JSONFormatter())
    logger.addHandler(log_handler)

    # Otherwise the root logger emits a second, unstructured copy of every line.
    logger.propagate = False


def log_unhandled_error(request, exc, endpoint: str = "", company_id: str = ""):
    """
    Record an unhandled exception where the alert policy can see it.

    Structured, with the exception type in its own field, so
    `severity>=ERROR` matches it and the count can be grouped by what broke.
    """
    logger.error(
        f"Unhandled error: {type(exc).__name__}",
        exc_info=exc,
        extra={
            "company_id": company_id or "",
            "endpoint": endpoint or getattr(getattr(request, "url", None), "path", ""),
            "extra_data": {
                "method": getattr(request, "method", ""),
                "error_class": type(exc).__name__,
            },
        },
    )

app = FastAPI()


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """
    Log every unhandled exception, then answer 500.

    Without this, an unhandled error is turned into a 500 by Starlette and the
    traceback goes to stderr unstructured — which is how the TypeError in the
    prediction path survived in production until the agent happened to narrate
    it on screen. Now it is one ERROR-severity line with the exception type in
    its own field, which is what the alert policy counts.

    The response body stays generic on purpose: the detail belongs in the log,
    not in an answer to whoever triggered it.
    """
    log_unhandled_error(request, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. The team has been notified."},
    )


BATCH_JOBS = {}

DATA_DIR = PROJECT_ROOT / "data"
if os.environ.get("K_SERVICE"):
    DATA_DIR = Path("/tmp/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "procurement.db"

# Application state goes through agent/db.py, which is Postgres in production
# and SQLite locally. Opening `sqlite3.connect(DB_PATH)` here instead wrote to
# DATA_DIR, and on Cloud Run that is /tmp — per-instance and wiped on cold
# start. Tracked outcomes, calendar events and predictions were being written
# to a disk that disappears.
from agent import db as _state_db
from agent import file_paths, object_store

#: PIDs whose schema has been ensured, so a forked child re-runs it against its
#: own connection. Same guard as agent/db.py's connector and the rate limiter:
#: this must not run at import, or the Cloud SQL connector's background threads
#: are built before the ASGI bridge forks.
_schema_ready: set = set()


def init_db():
    with _state_db.connect(DB_PATH) as conn:
        _init_db_schema(conn)


def _ensure_schema(conn):
    """Create the tables once per process, on first use rather than at import."""
    pid = os.getpid()
    if pid in _schema_ready:
        return
    _init_db_schema(conn)
    _schema_ready.add(pid)


def _init_db_schema(conn):
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

    # tracked_outcomes predates any notion of who owns a row, so every route
    # reading it returned every company's records. The column is added
    # additively: PRAGMA is SQLite-only, so table_columns answers the same
    # question on either backend.
    if "company_id" not in _state_db.table_columns(conn, "tracked_outcomes"):
        conn.execute("ALTER TABLE tracked_outcomes ADD COLUMN company_id TEXT")

    conn.commit()
    # No conn.close() here: agent/db.py's `with` block closes the connection,
    # unlike sqlite3. Closing it inside would shut it under the caller.

# init_db() is deliberately NOT called at import — see _schema_ready above.
# Each state-touching route calls _ensure_schema() on the connection it opens.

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

# CORS. This was `allow_origins=["*"]` with `allow_credentials=True`, which was
# harmless only because nothing was authenticated: there was no cookie worth
# stealing. Now that a session cookie exists it is not harmless — Starlette
# echoes the requesting origin back when a credentialed request arrives under a
# wildcard, so any site could have called these routes with the victim's cookie
# attached and read the response.
#
# The frontend is same-origin (Firebase Hosting rewrites /api/** to this
# function), so no browser origin needs to be listed for the app to work. The
# entries below exist for local development and for the deployed origins.
# CORS_ALLOWED_ORIGINS overrides the list; it is public configuration, so it
# belongs in .env, not Secret Manager.
_cors_env = (os.environ.get("CORS_ALLOWED_ORIGINS") or "").strip()
ALLOWED_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()] or [
    "https://cairoai.web.app",
    "https://cairoai.firebaseapp.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:8099",
    "http://127.0.0.1:8099",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure upload folders exist
UPLOAD_FOLDER = DATA_DIR / "archive"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
# The archive used to be DATA_DIR / "company_archive.json" — a flat list with
# no company_id in it, on a path that resolves to /tmp on Cloud Run. It is a
# table keyed by company now; see agent/memory/company_archive.py. The constant
# is kept only so the migration script can find any file left behind.
ARCHIVE_JSON_PATH = DATA_DIR / "company_archive.json"

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

from agent.memory import company_archive as _company_archive


def get_archived_companies(company_id):
    """
    This company's archived companies.

    `company_id` is now required. The previous signature took no argument and
    returned every company's records to whoever asked — five routes read it,
    including the compliance dashboard.

    The disk scan that used to live here is gone. It walked the shared
    UPLOAD_FOLDER and attached unassociated PDFs to a company by name match
    across all companies, falling back to "if only one company exists,
    associate it there". A file nobody has claimed is recoverable; a file
    attached to the wrong company is a disclosure.
    """
    return _company_archive.get_archived_companies(company_id)


def save_archived_companies(companies, company_id):
    """Replace this company's archive. Scoped: no other tenant's rows move."""
    return _company_archive.save_archived_companies(companies, company_id)


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
    """
    What the prediction model is worth, read from the metrics file.

    The AUCs here used to be hardcoded literals — 0.857833 for ZA, marked
    LOCKED_PRODUCTION_BASELINE — that no code could refresh and no measurement
    backed. 0.857833 came from `metrics_conquest_za.json`, whose `auc_val` and
    `auc_test` are the same number on 1,079 rows; the model actually serving
    predictions scores 0.5567 on 13,121 held-out rows.

    `model_validation` reads the metrics file that belongs to the model in use,
    so this cannot drift away from it again.
    """
    return {
        "regions": {
            "ZA": {"region": "South Africa (ZA)", "framework": "PPPFA 80/20 & 90/10"},
            "UK": {"region": "United Kingdom (GB)", "framework": "MEAT PCR 2015"},
        },
        "model_validation": model_validation.validation_status(),
    }

@app.get("/invite")
async def serve_invite_page():
    """
    The page an invitation link opens. Unauthenticated by necessity — the person
    opening it does not have an account yet, which is the point. It carries no
    secrets: the token is in the URL the recipient already holds, and the page
    cannot learn anything without it.
    """
    return FileResponse("static/invite.html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/reset")
async def serve_reset_page():
    """The page a password-reset link opens. Unauthenticated by necessity —
    the whole point is that the person cannot sign in."""
    return FileResponse("static/reset.html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/workspace")
async def serve_workspace_page():
    return FileResponse("static/workspace.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/company-profile")
async def serve_company_profile_page():
    """
    The Agent Autofill questionnaire wizard.

    The page has existed since the wizard was built and had no route, so it
    404'd — and because Hosting's catch-all rewrite serves index.html for an
    unknown path (CLAUDE.md trap 1), it did so as an HTTP 200 showing the wrong
    page rather than an obvious error.
    """
    return FileResponse("static/company_profile.html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


# Sign in, sign out, and device pairing. Mounted without a try/except, unlike
# the two optional routers below: if authentication fails to load, the correct
# behaviour is for the app not to start. Serving every other route with the
# gate missing is how this application got into the state this work is fixing.
from agent.auth_routes import router as auth_router

app.include_router(auth_router)

# The wizard's backend. Mounted here rather than defined in app.py so the
# questionnaire stays self-contained; without this every /api/questionnaire/*
# call the page makes returns 404.
try:
    from agent_autofill.questionnaire_api import router as questionnaire_router

    app.include_router(questionnaire_router)
except Exception:
    # Same reasoning as tool_dispatch's lazy imports: a partially built
    # agent_autofill package must not stop the rest of the app from serving.
    logger.exception("Agent Autofill questionnaire router unavailable")

# The Autofill Vault: upload a tender's returnable forms as one pack, submit,
# and review the whole thing in one place. This is the manual-upload stopgap
# that ships ahead of the cloud-monitoring version (HANDOFF.md §6). Same
# try/except as the routers below — a partially built agent_autofill package
# must not stop the rest of the app from serving — but note the difference from
# the auth router above, which is mounted unguarded on purpose.
try:
    from agent_autofill.pack_api import router as autofill_pack_router

    app.include_router(autofill_pack_router)
except Exception:
    logger.exception("Agent Autofill pack routes unavailable")

# Connecting a Drive or Dropbox account. The providers could already build a
# consent URL and exchange a code, but nothing called either — so there was no
# way to connect anything, and the CSRF `state` both providers generate was
# never stored or checked.
try:
    from agent_autofill.providers.oauth_routes import router as provider_oauth_router

    app.include_router(provider_oauth_router)
except Exception:
    logger.exception("Agent Autofill provider OAuth routes unavailable")


class QuotationRequest(BaseModel):
    supplier_name: str
    tender_title: str
    line_items: List[dict]
    evaluation_system: Optional[str] = "80/20"
    lowest_price: Optional[float] = None

@app.post("/api/generate-quotation")
async def api_generate_quotation(request: Request,
                                 principal: Principal = Depends(require_principal)):
    """
    Generates an official South African PDF quotation for a tender, with support for direct PDF uploads or JSON.

    Authenticated because it writes a file to disk and is reachable from the
    public internet. It resolves no company — the supplier comes from the
    request — so it was not one of the X-Company-ID call sites, but an
    unauthenticated route that produces artefacts is its own problem.
    """
    logger.info("Generate Quotation Request", extra={"endpoint": "/api/generate_quotation"})
    try:
        content_type = request.headers.get("content-type", "")
        supplier_name = "CAIROAI"
        tender_title = "Procurement Tender Quotation"
        evaluation_system = "80/20"
        line_items = []
        lowest_price = None
        # Defined on every path: the multipart branch never sets it, and the
        # renderer below reads client details and the RFQ reference from it.
        body = {}

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

                    # No price is synthesised here. This block used to read
                    #
                    #     tender_val = parsed_tender.get("tender_value") or 798116.25
                    #     subtotal_est = float(tender_val) / 1.15
                    #     ... unit_price: subtotal_est * 0.75  /  * 0.25
                    #
                    # so a tender with no extractable price produced a quotation
                    # for R798 116,25, split 75/25 into two line items to look
                    # considered, typeset, and ready to send to an organ of
                    # state. That is the exact failure price_search.py was
                    # rewritten to remove; see its docstring.
                    #
                    # The invented figure also chose the statute on the next
                    # line — 90/10 above R50m — so a number from nowhere decided
                    # which law the bid was evaluated under.
                    tender_val = parsed_tender.get("tender_value")
                    evaluation_system = get_evaluation_system(tender_val) or evaluation_system

                    # One line, no price. quote_document renders unit_price None
                    # as TBC, leaves it out of the subtotal, and prints "This
                    # quotation is incomplete." A person fills it in.
                    line_items = [{
                        "description": f"Supply and delivery per {tender_title[:60]} specification"
                                       if tender_title else "Supply and delivery per tender specification",
                        "qty": 1,
                        "unit_price": None,
                    }]
                finally:
                    if temp_path.exists():
                        temp_path.unlink()

            if not line_items:
                # Same rule with no document at all: a placeholder line for a
                # person to price, not a placeholder price.
                line_items = [{"description": "Professional goods and service delivery",
                               "qty": 1, "unit_price": None}]
            if not tender_title:
                tender_title = "Procurement Tender Quotation"
        else:
            body = await request.json()
            supplier_name = body.get("supplier_name", "CAIROAI")
            tender_title = body.get("tender_title", "Tender Quotation")
            line_items = body.get("line_items", [{"description": "Services",
                                                  "qty": 1, "unit_price": None}])
            evaluation_system = body.get("evaluation_system", "80/20")
            lowest_price = body.get("lowest_price")

        companies = get_archived_companies(principal.company_id)
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

        # The company_profile row is the authority on who this supplier is —
        # it is what carries the VAT number, the branding and the signatory.
        # The archive lookup above stays as a fallback for a company that has
        # documents archived but no profile filled in yet.
        from agent.memory.company_store import get_company_profile
        from agent.quotation.quote_document import render_quotation

        profile = get_company_profile(principal.company_id) or {}
        company = {**supplier_info, **{k: v for k, v in profile.items() if v}}

        client = body.get("client") or {}

        render_quotation(
            out_path,
            company=company,
            client=client,
            reference=body.get("reference", ""),
            subject=tender_title,
            line_items=line_items,
            date_text=datetime.now().strftime("%d %B %Y"),
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
    target_block: str = Form("CSD_CERT"),
    company_id: str = Depends(require_company_id),
):
    """
    Accepts a Compliance Vault document: stores it, classifies it, auto-sorts a
    misfiled one into the block it actually belongs to, and records it against
    the company.

    NOTE: this used to save and classify the file and then stop. It never called
    add_company_document, so an upload returned "success" and the document then
    did not appear in the vault, did not count toward the five required
    documents, and did not trigger the onboarding vet.

    The company is now the authenticated one. It used to be whatever the caller
    put in a header, which meant a document could be filed into any company's
    vault by anyone who could reach this route.
    """
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
async def api_get_companies(principal: Principal = Depends(require_principal)):
    """
    Returns the list of companies in the archive.

    The archive is a single shared JSON file rather than per-tenant state, so
    this is not tenant isolation — it is the difference between the supplier
    list being public and being behind a login. Making the archive tenant-scoped
    is separate work; see the note in the auth section of CLAUDE.md.
    """
    return get_archived_companies(principal.company_id)

@app.get("/api/company-profile")
async def api_company_profile(company_id: str = Depends(require_company_id)):
    """
    This company's profile and track record, for the agent sidebar.

    Every field here was hardcoded, and the code said so:

        # In a real app this would query the DB. We'll return mock data
        return {"name": "CairoAI", "registration": "2026/250499/07", ...}

    So every user saw the same company name, the same registration number, and
    the same invented track record — 3 wins at a 21% win rate against 2
    buyers — regardless of who they were. The whole workspace sidebar reads
    this endpoint.

    The profile now comes from `company_store`, which is the stated single
    source of truth for company facts, and the tier from `subscription`.

    The track record comes from tracked_outcomes: outcomes this company
    actually reported through the product. That is the only real record of
    their bidding that exists here. It is not the `pit_*` features the mock
    borrowed its field names from — those describe suppliers in the historical
    procurement dataset, not this customer, and `model_validation` records that
    they are near-empty anyway (pit_is_incumbent averages 0.007 because almost
    no bidder recurs).

    Incumbency is not reported at all. It needs the buying entity per bid, and
    tracked_outcomes has no buyer column, so there is nothing to count.
    """
    profile = company_store.get_company_profile(company_id) or {}
    tier = get_company_tier(company_id)

    location = ", ".join(
        part for part in (profile.get("registered_municipality"), profile.get("province"))
        if part
    )

    with _state_db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        cur = conn.execute(
            "SELECT actual_outcome FROM tracked_outcomes WHERE company_id = ?",
            (company_id,),
        )
        outcomes = [r["actual_outcome"] for r in cur.fetchall()]

    won = sum(1 for o in outcomes if o == "won")
    lost = sum(1 for o in outcomes if o == "lost")
    decided = won + lost

    # A win rate over zero decided bids is 0/0. Reporting it as "0%" would say
    # this company loses everything, which is a different claim from "they have
    # not recorded an outcome yet".
    win_rate = f"{round(100 * won / decided)}%" if decided else None

    bbbee_level = profile.get("bbbee_level")

    return {
        "name": profile.get("company_name"),
        "registration": profile.get("registration_number"),
        "location": location or None,
        "tier": tier,
        # True when company_store holds nothing for this company yet, so the
        # sidebar can prompt rather than render a row of blanks.
        "profile_empty": not profile,
        "stats": {
            "pit_total_wins": won if outcomes else None,
            "pit_win_rate_overall": win_rate,
            "bbbee_level": f"Lvl {bbbee_level}" if bbbee_level else None,
            # Needs the buying entity per bid; tracked_outcomes has no buyer.
            "pit_is_incumbent": None,
            "decided_outcomes": decided,
            "tracked_outcomes": len(outcomes),
        },
    }

@app.post("/api/company-profile")
async def api_update_company_profile(request: Request,
                                     company_id: str = Depends(require_company_id)):
    """
    Set up or change this company's profile from the app.

    P0-2. There was no write route at all: every field the product depends on —
    name, registration number, VAT number, addresses, contact details,
    signatory — could only be set by running Python against the database. That
    blocked both journeys, because autofill fills bid forms FROM the profile and
    the quotation renderer takes its letterhead and signatory from it.

    THE CONFIRMATION GATE IS PRESERVED, NOT BYPASSED

    This is a two-step route, mirroring `update_company_profile` exactly:

        POST {"fields": {...}}                    -> 200, the diff, nothing written
        POST {"fields": {...}, "confirmed": true} -> 200, written

    `confirmed` is read from the request and passed through. It is never
    defaulted to true here. company_store's docstring is explicit that
    confirmed=True asserts a human was shown these specific values and approved
    them, and that it must not be hard-coded by a caller that has shown the
    user nothing — so the first call returns `changes` for the page to display,
    and only the second writes.

    That is the same shape the agent already follows, so both paths obey one
    gate rather than two implementations of it.
    """
    body = await request.json()
    fields = (body or {}).get("fields")
    confirmed = bool((body or {}).get("confirmed"))

    if not isinstance(fields, dict) or not fields:
        raise HTTPException(status_code=400,
                            detail="Send a 'fields' object with the values to set.")

    try:
        if not confirmed:
            # Nothing is written. The page shows this and asks.
            preview = company_store.preview_company_profile_update(company_id, fields)
            return {"status": "preview", "written": False, **preview}

        result = company_store.update_company_profile(company_id, fields, confirmed=True)
    except ValueError as exc:
        # assert_no_signature_asset raises this: a signature is never a profile
        # field, and CairoAI never signs anything.
        raise HTTPException(status_code=400, detail=str(exc))

    if not result.get("written"):
        raise HTTPException(status_code=400,
                            detail=result.get("message") or "The profile was not written.")

    logger.info("Company profile updated", extra={
        "company_id": company_id, "endpoint": "/api/company-profile",
        "extra_data": {"fields": sorted(fields)},
    })
    return result


@app.get("/api/company-profile/fields")
async def api_company_profile_fields(company_id: str = Depends(require_company_id)):
    """
    Which fields the profile form may set, and what is in them now.

    Read from PROFILE_WRITABLE_FIELDS rather than duplicated in the page, so a
    field added to the store appears in the form without anyone remembering to
    add it — the drift that left six fields unreachable from the agent.
    """
    current = company_store.get_company_profile(company_id) or {}
    return {
        "company_id": company_id,
        "profile_exists": bool(current),
        "writable_fields": list(company_store.PROFILE_WRITABLE_FIELDS),
        "values": {f: current.get(f) for f in company_store.PROFILE_WRITABLE_FIELDS},
    }


# --- company logo -------------------------------------------------------------
#
# P1-4. `logo_file_path` existed on the profile and quote_document.py drew it,
# but nothing anywhere set it. The only way to get a logo onto a quotation was
# to write a filesystem path into the database by hand — and on Cloud Run that
# path is per-instance and vanishes on the next cold start.

#: Magic bytes, not extensions. The codebase already learned this lesson with
#: the seven fixtures named .docx that are actually OLE2 .doc: a name is a
#: claim, and this one is made by whoever is uploading.
_IMAGE_MAGIC = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpg",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
}

#: A logo is drawn at 54x54 points. Anything approaching this is already far
#: more than that needs, and the cap is what stops an upload route becoming a
#: way to fill the disk.
MAX_LOGO_BYTES = 2 * 1024 * 1024


def _image_kind(head: bytes) -> str | None:
    """The format these bytes actually are, or None if they are not an image."""
    for magic, kind in _IMAGE_MAGIC.items():
        if head.startswith(magic):
            return kind
    # WEBP is RIFF....WEBP, so the marker is not at offset 0.
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def company_logo_path(company_id: str, filename: str):
    """
    Where this company's logo is on THIS instance, restoring it if needed.

    The profile stores a bare filename rather than an absolute path, because an
    absolute path recorded on one instance means nothing on the next. This
    resolves it, pulling from the bucket when the local copy is missing — the
    same pattern generated documents already use.
    """
    local = file_paths.generated_dir() / filename
    if local.exists():
        return local
    if object_store.ensure_local(filename, local):
        return local
    return None


@app.post("/api/company-profile/logo")
async def api_upload_company_logo(logo: UploadFile = File(...),
                                  company_id: str = Depends(require_company_id)):
    """
    Upload the logo that appears on this company's quotations.

    Stored in Cloud Storage via object_store, not on the instance disk, so it
    survives a cold start. The profile records the stored FILENAME; the renderer
    resolves it through `company_logo_path`.

    Writing logo_file_path goes through update_company_profile with
    confirmed=True, and that is legitimate here rather than a bypass of the
    gate: the person has just chosen this exact file in a file picker, which is
    the confirmation. The gate exists to stop a model writing a half-remembered
    VAT number, not to make a user confirm a file they just selected. No other
    field is written.
    """
    raw = await logo.read()

    if not raw:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(raw) > MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"That logo is {len(raw) // 1024} KB. The limit is "
                   f"{MAX_LOGO_BYTES // 1024} KB.")

    kind = _image_kind(raw[:16])
    if kind is None:
        # Deliberately checked by content. A .png that is actually a PDF, or a
        # script, would otherwise be stored and handed to the PDF renderer.
        raise HTTPException(
            status_code=400,
            detail="That file is not an image. PNG, JPEG, GIF or WEBP only.")

    stored_name = f"logo_{company_id}_{uuid.uuid4().hex[:8]}.{kind}"
    local = file_paths.generated_dir() / stored_name
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(raw)

    durable = object_store.upload(local, stored_name)
    if not durable:
        # object_store.upload never raises and returns False when the bucket is
        # not configured. Locally that is normal; on Cloud Run it means the
        # logo is on one instance and will vanish, so say so rather than
        # reporting a success that decays.
        logger.warning("Logo stored only on this instance", extra={
            "company_id": company_id, "endpoint": "/api/company-profile/logo",
            "extra_data": {"filename": stored_name},
        })

    result = company_store.update_company_profile(
        company_id, {"logo_file_path": stored_name}, confirmed=True)
    if not result.get("written"):
        raise HTTPException(status_code=400,
                            detail=result.get("message") or "Could not record the logo.")

    return {
        "status": "success",
        "filename": stored_name,
        "kind": kind,
        "bytes": len(raw),
        "durable": durable,
    }


@app.post("/api/companies/upload")
async def api_upload_company_file(
    file: List[UploadFile] = File(...),
    target_company: Optional[str] = Form(""),
    expiry_date: Optional[str] = Form(None),
    principal: Principal = Depends(require_principal),
):
    """Uploads CIPC/CSD documents (multiple supported), parses them, and adds/updates companies in the archive"""
    logger.info("Company File Upload", extra={"endpoint": "/api/companies/upload", "extra_data": {"files_count": len(file), "target_company": target_company}})
    if not file or all(f.filename == "" for f in file):
        raise HTTPException(status_code=400, detail="No file uploaded or selected files have no filenames")
        
    target_comp = target_company.upper().strip() if target_company else ""
    
    companies = get_archived_companies(principal.company_id)
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
        
    save_archived_companies(companies, principal.company_id)
    
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
async def api_vault_status(company_id: str = Depends(require_company_id)):
    """
    Which of the required Compliance Vault documents this company has.

    The Agent page polls this to decide whether the vault is complete enough to
    offer vetting, so it can say what is still outstanding instead of the old
    hardcoded "Auditing vault..." animation that ran on a timer and checked
    nothing.
    """
    from agent.tool_dispatch import _get_vault_status

    try:
        return _get_vault_status(company_id)
    except Exception:
        logger.exception("Vault status lookup failed", extra={"company_id": company_id})
        raise HTTPException(status_code=500, detail="Could not read vault status")


@app.get("/api/generated/{filename}")
async def api_serve_generated(filename: str,
                              principal: Principal = Depends(require_principal)):
    """
    Serves anything the agent generated (quotation PDFs, accreditation reports).

    OWNERSHIP IS CHECKED HERE. This route verified that an Agent Autofill
    export's stamp was genuine and never checked that the caller was entitled
    to the document — a filename was sufficient authority. The stamp answers
    "is this what it claims to be"; it cannot answer "is this yours", because
    the file does not know who is asking.

    The uuid4 in each filename makes guessing impractical, but a filename
    travels: chat transcripts, browser history, screenshots, support tickets,
    access logs. Unguessable is not private, and these documents carry
    registration and tax numbers, director ID numbers, and the bid itself.

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

    # OWNERSHIP FIRST, then look for the bytes.
    #
    # This used to check existence first, which was fine while the file was
    # only ever on local disk. It is not fine now that a miss can trigger a
    # fetch from durable storage: checking ownership afterwards would let
    # anyone holding a filename cause an object to be pulled out of the bucket.
    # The order below means an unauthorised caller never reaches storage at all.
    #
    # Same 404 for "not yours" as for "does not exist". A distinct 403 would
    # confirm the file is real to someone holding only a filename.
    from agent.generated_files import belongs_to

    if not belongs_to(filename, principal.company_id):
        logger.warning(
            "refused generated file %s for company %s (not the owner, or "
            "no owner recorded)", filename, principal.company_id)
        raise HTTPException(status_code=404, detail="File not found or expired")

    # `/tmp` is per-instance, so the instance answering this request is very
    # often not the one that produced the file. Restore it from the bucket
    # rather than telling the owner their document expired — which is what
    # happened to a real filled SBD 1 ten minutes after it was generated.
    if not file_path.exists():
        from agent import object_store

        object_store.ensure_local(filename, file_path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found or expired")

    # An Agent Autofill export is checked against its review record before it
    # leaves the building. A file whose stamp claims REVIEWED but does not
    # verify has either been altered or carries a stamp copied from another
    # document, and handing it to someone about to submit it to an organ of
    # state is the worst outcome this system has. Files that are not autofill
    # exports — quotation PDFs, accreditation reports — return None here and
    # pass through untouched.
    try:
        from agent_autofill.integration.review_gate import verify_export_by_path

        verdict = verify_export_by_path(file_path)
    except Exception:
        # A verification that cannot run must not silently become a pass, but
        # it also must not take down the route for unrelated downloads.
        logger.exception("Export verification failed to run for %s", filename)
        verdict = None

    if verdict is not None and not verdict.get("mac_verified"):
        logger.error(
            "Refusing to serve unverifiable export %s: %s",
            filename, verdict.get("mac_detail", "no detail"),
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "This document does not match the review it claims. It may have "
                "been edited after export, or it predates document verification. "
                "Re-export it from the review before using it."
            ),
        )

    # This used to hardcode application/pdf. Agent Autofill exports .docx drafts
    # through the same route, and a Word document served as a PDF makes the
    # browser try to render it inline and fail.
    import mimetypes

    media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return FileResponse(str(file_path), media_type=media_type)


@app.delete("/api/companies/{company_name}")
async def api_delete_company(company_name: str,
                             principal: Principal = Depends(require_principal)):
    """
    Deletes a company and its files from the archive.

    This route unlinks files from disk and answered to anyone who could reach
    it. It resolves no company_id, so it was not one of the header call sites —
    which is exactly why it is easy to miss.
    """
    companies = get_archived_companies(principal.company_id)
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
                
    save_archived_companies(companies, principal.company_id)
    
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
    model_version: Optional[str] = Form("sailor"),
    company_id: str = Depends(require_company_id),
):
    """
    Submits a Tender PDF & Bid PDF:
    - Parses Bid PDF to find matching archived company name.
    - Parses Bid PDF and Tender PDF for pricing.
    - If found, retrieves B-BBEE Level.
    - Queries prediction pipeline and preferential scoring logic.

    The model allow-list below is a tier gate. It is only a gate now that
    `company_id` is the authenticated company: while it came from a header,
    "conquest is not on your plan" was advice, not enforcement — a starter
    account could ask for it by claiming to be someone else.
    """
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
    companies = get_archived_companies(company_id)
    
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
        
    # Impute missing pricing.
    #
    # The supplier's own price is imputed because the ML pipeline needs a
    # number in the feature vector and the model's caveat already covers what
    # its output is worth. `lowest_price` is NOT imputed: it feeds the PPPFA
    # price score, which is presented as a calculation under the regulations
    # rather than as a model output. `supplier_price * 0.88` made every bidder
    # 13.6% above a competitor who did not exist.
    if supplier_price is None:
        supplier_price = 450000.0

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
            with _state_db.connect(DB_PATH) as conn:
                _ensure_schema(conn)
                c = conn.cursor()
                now = datetime.now().isoformat()
                c.execute('''INSERT INTO tracked_outcomes (id, prediction_id, tender_identifier, filename, supplier_name, predicted_probability, sa_adjusted_probability, recommendation, actual_outcome, outcome_date, notes, created_at, updated_at, company_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                            (str(uuid.uuid4()), prediction_id, tender_id, tender_file.filename if tender_file else "", name_to_use, None, None, "DISQUALIFIED", "pending", None, "", now, now, company_id))
                conn.commit()

            from models.sa_scoring import (
                get_evaluation_system, calculate_price_score, get_bbbee_points,
                NO_COMPETING_PRICE, NO_TENDER_VALUE,
            )
            disq_eval_sys = get_evaluation_system(tender_value)
            # None, not 0.0, when there is no competing price to score against.
            # A price score of zero means "you are far above the lowest bid" —
            # the opposite of "we could not work it out".
            disq_price_pts = calculate_price_score(supplier_price, lowest_price, disq_eval_sys)
            disq_bbbee_pts = get_bbbee_points(bbbee_to_use, disq_eval_sys)
            # Both can be None: the price score without a competing price, the
            # B-BBEE points without a tender value to say which system applies.
            disq_total = (None if (disq_price_pts is None or disq_bbbee_pts is None)
                          else disq_price_pts + disq_bbbee_pts)
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
                    "price_score": None if disq_price_pts is None else round(disq_price_pts, 4),
                    "price_score_available": disq_price_pts is not None,
                    "price_score_unavailable_reason": None if disq_price_pts is not None else NO_COMPETING_PRICE,
                    "bbbee_points": None if disq_bbbee_pts is None else float(disq_bbbee_pts),
                    "max_bbbee_points": get_bbbee_points(1, disq_eval_sys),
                    "evaluation_system_unavailable_reason": None if disq_eval_sys else NO_TENDER_VALUE,
                    "total_score": None if disq_total is None else round(disq_total, 4),
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
            "price_score_available": sa_score["price_score_available"],
            "price_score_unavailable_reason": sa_score["price_score_unavailable_reason"],
            "bbbee_points": sa_score["bbbee_points"],
            "max_bbbee_points": sa_score["max_bbbee_points"],
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

        # Without a PPPFA score there is no adjusted probability, and PURSUE /
        # PASS is a recommendation about money derived from a number we do not
        # have. Withheld, with the reason attached.
        if final_probability is None:
            recommendation = None
            confidence = None
        else:
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
        
        with _state_db.connect(DB_PATH) as conn:
            _ensure_schema(conn)
            c = conn.cursor()
            now = datetime.now().isoformat()
            c.execute('''INSERT INTO tracked_outcomes (id, prediction_id, tender_identifier, filename, supplier_name, predicted_probability, sa_adjusted_probability, recommendation, actual_outcome, outcome_date, notes, created_at, updated_at, company_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                        (str(uuid.uuid4()), prediction_id, tender_id, tender_file.filename if tender_file else "", name_to_use, base_prob, final_probability, recommendation, "pending", None, "", now, now, company_id))
            conn.commit()

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
            # What that number is worth, travelling with it. The model scores
            # ~0.53-0.56 AUC on held-out data; presented bare, a percentage
            # reads as a measurement.
            "model_validation": model_validation.validation_status(),
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

async def process_batch_job(job_id: str, file_paths: list, filenames: list, name_to_use: str,
                            bbbee_to_use: int, target_artifacts: dict, company_id: str):
    """
    Score a batch in the background.

    `company_id` is required and has no default deliberately. Every row this
    writes to tracked_outcomes is owned by it, and a default here would put
    a guessed owner on real records — the same hole `require_company_id`
    exists to close on the read side.
    """
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
                
            # Impute missing pricing. This path overwrote `supplier_price`
            # unconditionally, so a price actually parsed from the document was
            # discarded and every tender in a batch was scored as R450,000
            # against R396,000. Use what was parsed when there is something.
            supplier_price = parsed_tender.get("bid_price") or 450000.0
            lowest_price = parsed_tender.get("lowest_price")

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
            # Same rule as the single path: no score, no recommendation.
            recommendation = (
                None if final_probability is None
                else ("PURSUE" if final_probability >= threshold else "PASS")
            )

            prediction_id = str(uuid.uuid4())
            with _state_db.connect(DB_PATH) as conn:
                _ensure_schema(conn)
                c = conn.cursor()
                now = datetime.now().isoformat()
                c.execute('''INSERT INTO tracked_outcomes (id, prediction_id, tender_identifier, filename, supplier_name, predicted_probability, sa_adjusted_probability, recommendation, actual_outcome, outcome_date, notes, created_at, updated_at, company_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                            (str(uuid.uuid4()), prediction_id, tender_id, filename, name_to_use, base_prob, final_probability, recommendation, "pending", None, "", now, now, company_id))
                conn.commit()

            job["results"].append({
                "prediction_id": prediction_id,
                "filename": filename,
                "tender_identifier": tender_id,
                "disqualified": False,
                "hard_failures": [],
                "win_probability": base_prob,
                "sa_adjusted_probability": final_probability,
                "model_validation": model_validation.validation_status(),
                "recommendation": recommendation,
                "competitive_position": sa_score["competitive_position"],
                "price_score_available": sa_score["price_score_available"],
                "price_score_unavailable_reason": sa_score["price_score_unavailable_reason"],
                "bbbee_points": sa_score["bbbee_points"],
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
    model_version: Optional[str] = Form("sailor"),
    company_id: str = Depends(require_company_id),
):
    if not files or all(f.filename == "" for f in files):
        raise HTTPException(status_code=400, detail="No files uploaded")

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
    companies = get_archived_companies(company_id)
    
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
        
    background_tasks.add_task(process_batch_job, job_id, file_paths, filenames, name_to_use, bbbee_to_use, target_artifacts, company_id)
    
    return {"job_id": job_id}

@app.get("/api/batch-status/{job_id}")
async def api_batch_status(job_id: str):
    if job_id not in BATCH_JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    return BATCH_JOBS[job_id]

@app.post("/api/track-outcome")
async def track_outcome(req: TrackOutcomeRequest,
                        company_id: str = Depends(require_company_id)):
    """
    Record the real outcome of a prediction.

    This was an unauthenticated write, and its lookup and UPDATE were keyed on
    `prediction_id` alone. Anyone who could guess or observe a prediction_id
    could overwrite another company's recorded outcome — or insert rows into
    their table. It is not in LAUNCH_PLAN's A1 list, which covers the three
    read endpoints; the write side had the same hole.

    Both statements are scoped by company_id now, so a prediction_id belonging
    to someone else simply does not match, and the request falls through to
    inserting a row owned by the caller rather than editing a stranger's.
    """
    with _state_db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        c = conn.cursor()
        now = datetime.now().isoformat()

        c.execute(
            "SELECT id FROM tracked_outcomes WHERE prediction_id = ? AND company_id = ?",
            (req.prediction_id, company_id),
        )
        row = c.fetchone()
        if row:
            c.execute("""
                UPDATE tracked_outcomes
                SET actual_outcome = ?, outcome_date = ?, notes = ?, updated_at = ?
                WHERE prediction_id = ? AND company_id = ?
            """, (req.actual_outcome, req.outcome_date, req.notes, now, req.prediction_id, company_id))
        else:
            c.execute("""
                INSERT INTO tracked_outcomes (id, prediction_id, tender_identifier, filename, supplier_name, predicted_probability, sa_adjusted_probability, recommendation, actual_outcome, outcome_date, notes, created_at, updated_at, company_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (str(uuid.uuid4()), req.prediction_id, req.tender_identifier, req.filename, req.supplier_name, req.predicted_probability, req.sa_adjusted_probability, req.recommendation, req.actual_outcome, req.outcome_date, req.notes, now, now, company_id))
        conn.commit()
    return {"status": "success"}

@app.get("/api/accuracy-stats")
async def get_accuracy_stats(company_id: str = Depends(require_company_id)):
    """
    How this company's predictions turned out.

    Same hole as /api/tracked-outcomes: unauthenticated, and `SELECT *` with no
    WHERE clause. Aggregates are not anonymous — a hit rate computed over every
    company's bids is still other companies' data, and with few customers it is
    close to reading their records directly.
    """
    with _state_db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        c = conn.cursor()
        c.execute("SELECT * FROM tracked_outcomes WHERE company_id = ?", (company_id,))
        rows = c.fetchall()
    
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
async def get_tracked_outcomes(principal: Principal = Depends(require_principal)):
    """
    This company's tracked outcomes.

    Was unauthenticated and unfiltered: `SELECT * FROM tracked_outcomes` with no
    WHERE clause, reachable by any anonymous caller. It returned [] only because
    the table was empty — the moment a customer tracked an outcome, every other
    customer could read it.

    Rows written before company_id existed carry NULL and belong to nobody, so
    they match no principal and are returned to no one. That is deliberate:
    invisible is the safe direction, and guessing an owner would be inventing
    one.
    """
    with _state_db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        c = conn.cursor()
        c.execute(
            "SELECT * FROM tracked_outcomes WHERE company_id = ? ORDER BY updated_at DESC",
            (principal.company_id,),
        )
        rows = [dict(r) for r in c.fetchall()]
    return rows

@app.get("/api/compliance-status")
async def get_compliance_status(principal: Principal = Depends(require_principal)):
    """
    Compliance status for this company's archived documents.

    This was the route where A1 could add authentication but not a tenant
    filter: `get_archived_companies()` read company_archive.json, a flat list
    with no company_id in it, so there was no key to filter on. A2 moved the
    archive into a table keyed by company, and the filter is now real rather
    than deferred.
    """
    companies = get_archived_companies(principal.company_id)
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
async def get_calendar_events(month: str = None,
                              principal: Principal = Depends(require_principal)):
    """
    This company's calendar events.

    Was unauthenticated and read the whole table. It happens to return [] today
    because `events` below is never populated — but the query was already
    cross-tenant, so the leak would have arrived with the feature rather than
    being noticed as a new bug. Filtered now, before that happens.
    """
    with _state_db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        c = conn.cursor()
        c.execute(
            "SELECT * FROM tracked_outcomes WHERE company_id = ?",
            (principal.company_id,),
        )
        rows = [dict(r) for r in c.fetchall()]
    
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
    
    # Measured, or absent. These used to default to 0.8578 / 0.8187 — figures
    # no run produced. Worse, the conquest branch read `auc_cb` from
    # metrics_conquest.json, which has no such key, so the `.get` chain fell
    # through to the 0.8578 literal even on the path that was "reading the real
    # metrics file". The held-out AUC in that file is 0.5567.
    test_auc = None
    last_trained = None
    n_features = None
    precision = None
    recall = None

    if metrics_path.exists():
        try:
            with open(metrics_path, "r") as f:
                metrics_data = json.load(f)
            test_section = metrics_data.get("test") or {}
            test_auc = test_section.get("roc_auc")
            n_features = metrics_data.get("n_features", metrics_data.get("feature_count"))
            last_trained = metrics_data.get("timestamp")
            precision = test_section.get("precision")
            recall = test_section.get("recall")
        except Exception as e:
            print(f"Error reading metrics JSON: {e}")
            
    # Count predictions from DB
    try:
        with _state_db.connect(DB_PATH) as conn:
            _ensure_schema(conn)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM tracked_outcomes")
            total_predictions = c.fetchone()[0]
    except Exception:
        # None, not 1420. An unreadable counter is not a busy one.
        total_predictions = None
        
    # `top_features` and `ensemble_models` were hardcoded tables — feature
    # importances to two decimal places that no model produced, and per-model
    # AUCs with blend weights for an ensemble whose composition was invented.
    # Real feature importances are obtainable from the loaded booster; until
    # something reads them, an empty list is the honest answer.
    top_features = []
    ensemble_models = []

    disp_version = "Conquest v1.0.0 (CatBoost Engine)" if is_conquest else "Sailor v2.1.0 (Ensemble)"

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
        # The real count. This was `max(1420, total_predictions)`, which floored
        # a genuine figure at a number chosen to look established.
        "total_predictions_made": total_predictions,
        # `total_companies_archived` was len(get_archived_companies()) — a
        # count across every tenant, on an endpoint that requires no
        # credential. How many companies your customers have archived is a
        # fact about them, not about the system, so it is not reported here.
        # A per-tenant count is available from an authenticated route.
        "calibration_method": "Isotonic Regression",
        "data_sources": ["GPPD (2018-2023)", "SA Treasury OCDS", "CIPC Ledger"],
        # What the AUC above is worth, travelling with it.
        "model_validation": model_validation.validation_status(),
    }
    
class EstimateRequest(BaseModel):
    tender_id: str

@app.post("/api/estimate")
async def api_estimate(req: EstimateRequest,
                       principal: Principal = Depends(require_principal)):
    # Authenticated because this one calls out to paid external APIs. An
    # unauthenticated endpoint that spends money on request is a bill, not a
    # feature.
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
async def api_subscription_status(company_id: str = Depends(require_company_id)):
    """
    The caller's own plan and usage.

    This one is worth naming: it used to report whatever plan the header asked
    for, so the page could read back "enterprise" simply by saying so, and every
    quota shown to the user was a quota for a company they had not proved they
    were.
    """
    return get_subscription_status(company_id)

class AgentChatRequest(BaseModel):
    message: str
    action: Optional[str] = None
    tender_file_path: Optional[str] = None

from agent.rate_limiter import check_global_rate_limit
import html

@app.post("/api/agent/chat")
async def api_agent_chat(
    request: Request,
    payload: AgentChatRequest,
    principal: Principal = Depends(require_principal),
):
    # LAYER 1: identity. The company is the authenticated one, so the tier
    # checks below are enforcement rather than a suggestion. Previously a
    # starter account reached Claude — and spent our Anthropic budget — by
    # sending X-Company-ID: pro_corp.
    company_id = principal.company_id
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
