"""
Autofill Vault — a pack is a group of uploaded documents reviewed as one thing.

WHY THIS EXISTS
---------------
The cloud-monitoring version of Agent Autofill is blocked (HANDOFF.md §6:
`drive.file` cannot watch a folder, Dropbox was dropped, the Android client has
no toolchain here). This is the manual-upload stopgap that ships in front of it:
the user uploads the returnable forms for one tender together, presses submit,
and gets ONE review covering all of them.

WHAT IT IS NOT
--------------
It is not a second autofill pipeline. Every per-file decision — classification,
extraction, alias matching, the fill engine, the review summary — belongs to
`main_autofill_orchestrator.run_autofill_batch`, which is already tier- and
quota-gated before it opens a file and already stops the moment the quota runs
out. This module calls it once per pack and does nothing that module does.

Likewise the review itself. `integration/review_gate.py` owns acknowledgement,
value confirmation and the export gate, per document. A pack is a *wrapper*
over one review per file: it fans a pack-level call out to the per-review
functions and fans their state back in. There is no pack-level copy of the gate,
because a second gate is a second thing to get wrong and only one of them would
be the one the export path consults.

THE ONE RULE IS UNCHANGED
-------------------------
Drafts only. Grouping documents does not lower the bar: a pack reaches
`reviewed` only when EVERY flagged field in EVERY document has been
acknowledged individually AND every pre-filled value in every document has been
confirmed. The pack status is derived from that persisted state on every read,
never set by a caller.

TWO TRAPS THIS MODULE IS SHAPED AROUND
--------------------------------------
1. **Never open a second connection inside an open transaction.** SQLite blocks
   the inner writer until the 30s busy timeout and then raises "database is
   locked"; this broke every reviewed export once already. Every review_gate
   function opens its own connection, so this module never calls one while
   holding a pack connection. Where a write and a registration must share a
   transaction, the connection is passed down (`register(..., conn=conn)`).

2. **Pack status lives in the database, not in a dict.** `BATCH_JOBS` in app.py
   is an in-memory dict and does not survive a Cloud Functions instance
   restart. A pack that is mid-processing when an instance recycles must still
   be answerable, so `status` is a column and the background task is only the
   thing that moves it. A pack is never left in `processing`: the worker's
   terminal states are written in a `finally`.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from agent import db
from agent.db_paths import AGENT_MEMORY_DB as DB_PATH
from agent.file_paths import generated_dir, pack_upload_dir, public_url
from agent.generated_files import register as register_generated
from agent_autofill.integration import pack_events
from agent.memory.company_store import get_company_profile
from agent.subscription import check_autofill_quota
from agent_autofill.extraction.legacy_doc_reader import detect_format
from agent_autofill.fill_engine.never_fill_fields import BLOCK_MESSAGES
from agent_autofill.integration.review_gate import (
    ReviewGateError,
    acknowledge_field,
    confirm_filled_values,
    export_reviewed,
    filled_values,
    get_review,
    open_review,
    verify_export,
)

log = logging.getLogger("agent_autofill.pack_store")

#: The five states a pack can be in. `processing` is the only non-terminal one
#: and the worker guarantees it is left.
STATUS_DRAFT = "draft"
STATUS_PROCESSING = "processing"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_REVIEWED = "reviewed"
STATUS_ERROR = "error"

#: How many documents one pack may hold. A tender's returnable-forms bundle is
#: a handful of files; a number this size exists to stop a single request
#: turning into an unbounded upload, not to ration anything.
MAX_FILES_PER_PACK = 40

#: Uploads are read into memory to be written, so this is a real ceiling rather
#: than advisory. SBD/MBD form packs are tens of kilobytes; a 145-page tender
#: PDF is a few megabytes.
MAX_FILE_BYTES = 25 * 1024 * 1024

#: What the fill engine refused, keyed by the exact message it records. The
#: messages are `never_fill_fields.BLOCK_MESSAGES`, so this maps back to the
#: engine's own vocabulary instead of re-deriving "is this a signature field"
#: from the label — which would be a second classifier disagreeing with the
#: first one the moment either changed.
_MESSAGE_TO_KIND = {message: reason.value for reason, message in BLOCK_MESSAGES.items()}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class PackError(Exception):
    """Base for the three refusals this module makes, so routes can map them."""


class PackNotFound(PackError):
    """No such pack for this company. Deliberately not distinguishable from
    'exists but belongs to someone else' — see `_load_pack`."""


class PackStateError(PackError):
    """The pack is real but is in the wrong state for this action (409)."""


class PackRefused(PackError):
    """The plan does not include Agent Autofill, or the day's quota is spent
    (403). Nothing is wrong with the pack, so it is not a state error."""


class PackInputError(PackError):
    """The request itself is malformed or empty (400)."""


# --- schema -----------------------------------------------------------------


def init_pack_db() -> None:
    with db.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS autofill_packs (
                pack_id      TEXT PRIMARY KEY,
                company_id   TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'draft',
                pack_name    TEXT,
                created_at   TIMESTAMP,
                submitted_at TIMESTAMP,
                completed_at TIMESTAMP,
                error_reason TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS autofill_pack_files (
                file_id           TEXT PRIMARY KEY,
                pack_id           TEXT NOT NULL,
                original_filename TEXT,
                file_type         TEXT,
                storage_path      TEXT,
                uploaded_at       TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_autofill_pack_files_pack
            ON autofill_pack_files (pack_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_autofill_packs_company
            ON autofill_packs (company_id, created_at)
        """)

        # Additive migrations. `db.table_columns` rather than PRAGMA, which does
        # not exist on Postgres (CLAUDE.md trap 7).
        pack_cols = db.table_columns(conn, "autofill_packs")
        # The eligibility / win-probability verdict for the pack's primary
        # tender document, stored as JSON so the detail view can return it
        # without re-running a pipeline that reads a 145-page PDF.
        if "assessment_json" not in pack_cols:
            conn.execute("ALTER TABLE autofill_packs ADD COLUMN assessment_json TEXT")
        if "primary_file_id" not in pack_cols:
            conn.execute("ALTER TABLE autofill_packs ADD COLUMN primary_file_id TEXT")

        file_cols = db.table_columns(conn, "autofill_pack_files")
        # The review this file produced, if it produced one. This is the join
        # to review_gate — a pack owns no review state of its own.
        if "review_id" not in file_cols:
            conn.execute("ALTER TABLE autofill_pack_files ADD COLUMN review_id TEXT")
        if "run_status" not in file_cols:
            conn.execute("ALTER TABLE autofill_pack_files ADD COLUMN run_status TEXT")
        if "run_message" not in file_cols:
            conn.execute("ALTER TABLE autofill_pack_files ADD COLUMN run_message TEXT")
        # Set for every file the worker reached, whatever the outcome. This is
        # what files_done counts, so a skipped or failed file still advances the
        # progress the user is watching instead of stalling it.
        if "processed_at" not in file_cols:
            conn.execute("ALTER TABLE autofill_pack_files ADD COLUMN processed_at TIMESTAMP")
        if "draft_url" not in file_cols:
            conn.execute("ALTER TABLE autofill_pack_files ADD COLUMN draft_url TEXT")
        if "summary_url" not in file_cols:
            conn.execute("ALTER TABLE autofill_pack_files ADD COLUMN summary_url TEXT")


init_pack_db()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- creation and editing ---------------------------------------------------


def _load_pack(company_id: str, pack_id: str):
    """
    Fetch a pack, pinned to the calling tenant.

    company_id is part of the WHERE clause rather than checked afterwards, so
    another company's pack_id returns nothing at all and the caller cannot even
    confirm it exists. The route turns this into 404, never 403: a 403 would
    tell a stranger the id is real.
    """
    if not company_id:
        raise PackInputError("A company_id is required — packs are always tenant-scoped.")
    with db.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM autofill_packs WHERE pack_id = ? AND company_id = ?",
            (pack_id, company_id),
        ).fetchone()
    if row is None:
        raise PackNotFound(f"No autofill pack {pack_id!r} belongs to this company.")
    return row


def create_pack(company_id: str, pack_name: str = "") -> dict:
    if not company_id:
        raise PackInputError("A company_id is required — packs are always tenant-scoped.")
    pack_id = str(uuid.uuid4())
    name = (pack_name or "").strip()[:200] or "Untitled pack"
    with db.connect(DB_PATH) as conn:
        conn.execute(
            """INSERT INTO autofill_packs
               (pack_id, company_id, status, pack_name, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (pack_id, company_id, STATUS_DRAFT, name, _now()),
        )
    return {"pack_id": pack_id}


def rename_pack(company_id: str, pack_id: str, pack_name: str) -> dict:
    _load_pack(company_id, pack_id)
    name = (pack_name or "").strip()[:200]
    if not name:
        raise PackInputError("A pack name cannot be empty.")
    with db.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE autofill_packs SET pack_name = ? WHERE pack_id = ? AND company_id = ?",
            (name, pack_id, company_id),
        )
    return {"pack_id": pack_id, "pack_name": name}


def _pack_files(pack_id: str) -> list:
    with db.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT * FROM autofill_pack_files WHERE pack_id = ? ORDER BY uploaded_at, file_id",
            (pack_id,),
        ).fetchall()


def add_files(company_id: str, pack_id: str, uploads) -> dict:
    """
    Store several uploaded documents against one pack, in one call.

    `uploads` is a sequence of (original_filename, content_bytes). Multiple
    files per request is the point: a tender's returnable forms arrive as a
    bundle and asking the browser to make eight sequential requests would make
    partial failure the normal case.

    A pack that has been submitted cannot gain files. Its review state is
    already derived from the files it had, and a document added afterwards would
    be in the pack, absent from the review, and invisible to the export gate —
    which is the one shape of bug the whole review flow exists to prevent.
    """
    pack = _load_pack(company_id, pack_id)
    if pack["status"] != STATUS_DRAFT:
        raise PackStateError(
            f"This pack is {pack['status']}, so files can no longer be added to it. "
            "Create a new pack for further documents."
        )

    uploads = list(uploads or [])
    if not uploads:
        raise PackInputError("No files were uploaded.")

    existing = len(_pack_files(pack_id))
    if existing + len(uploads) > MAX_FILES_PER_PACK:
        raise PackInputError(
            f"A pack holds at most {MAX_FILES_PER_PACK} documents "
            f"({existing} already uploaded, {len(uploads)} more offered)."
        )

    target_dir = pack_upload_dir(company_id, pack_id)
    stored: list[dict] = []
    rows = []
    now = _now()

    for original, content in uploads:
        original = (original or "").strip() or "document"
        if content is None:
            raise PackInputError(f"{original} arrived empty.")
        if len(content) > MAX_FILE_BYTES:
            raise PackInputError(
                f"{original} is {len(content) // (1024 * 1024)}MB; the limit is "
                f"{MAX_FILE_BYTES // (1024 * 1024)}MB per document."
            )
        if not content:
            raise PackInputError(f"{original} is empty.")

        file_id = str(uuid.uuid4())
        # The stored name is ours, not theirs: a uuid prefix so two uploads
        # called "SBD 4.docx" cannot overwrite each other, and the rest slugged
        # so nothing in a user-supplied name reaches the filesystem intact.
        suffix = Path(original).suffix.lower()[:10]
        slug = _SAFE_NAME.sub("_", Path(original).stem).strip("._-")[:60] or "document"
        stored_path = target_dir / f"{file_id[:8]}_{slug}{_SAFE_NAME.sub('', suffix)}"
        stored_path.write_bytes(content)

        # Magic bytes, never the extension. Seven of the sa_forms fixtures are
        # OLE2 `.doc` behind a `.docx` name and users really do rename them;
        # the orchestrator reports those as read-only rather than crashing.
        file_type = detect_format(stored_path)

        rows.append((file_id, pack_id, original, file_type, str(stored_path), now))
        stored.append({
            "file_id": file_id,
            "original_filename": original,
            "file_type": file_type,
        })

    with db.connect(DB_PATH) as conn:
        conn.executemany(
            """INSERT INTO autofill_pack_files
               (file_id, pack_id, original_filename, file_type, storage_path, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )
    return {"files": stored}


def remove_file(company_id: str, pack_id: str, file_id: str) -> dict:
    pack = _load_pack(company_id, pack_id)
    if pack["status"] != STATUS_DRAFT:
        raise PackStateError(
            f"This pack is {pack['status']}; its documents are part of a review "
            "and cannot be removed."
        )
    with db.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT storage_path FROM autofill_pack_files WHERE file_id = ? AND pack_id = ?",
            (file_id, pack_id),
        ).fetchone()
        if row is None:
            raise PackNotFound(f"No file {file_id!r} in this pack.")
        conn.execute(
            "DELETE FROM autofill_pack_files WHERE file_id = ? AND pack_id = ?",
            (file_id, pack_id),
        )
    try:
        Path(row["storage_path"]).unlink(missing_ok=True)
    except OSError:
        # The row is gone, which is what makes the file unreachable. A stray
        # byte on disk is not worth failing the request over.
        log.warning("could not delete pack file %s", row["storage_path"])
    return {"deleted": True, "file_id": file_id}


# --- submit -----------------------------------------------------------------


def submit_pack(company_id: str, pack_id: str) -> dict:
    """
    Move a pack to `processing`. Does no work and makes no API call.

    The tier/quota check here is `subscription.check_autofill_quota` — the exact
    same free database COUNT that `run_autofill` runs as its own first step, and
    which still runs there. It is called here as well so a Starter company gets
    a 403 from submit rather than a 200 followed by a pack that fails a second
    later. It is not a second paid path: nothing is classified, extracted or
    filled here, and `run_autofill_batch` remains the authority that refuses
    before a file is opened.
    """
    pack = _load_pack(company_id, pack_id)
    if pack["status"] == STATUS_PROCESSING:
        raise PackStateError("This pack is already being processed.")
    if pack["status"] != STATUS_DRAFT:
        raise PackStateError(
            f"This pack has already been submitted (status {pack['status']})."
        )

    files = _pack_files(pack_id)
    if not files:
        raise PackInputError("Add at least one document before submitting the pack.")

    quota = check_autofill_quota(company_id)
    if not quota.get("allowed"):
        raise PackRefused(quota.get("reason", "Agent Autofill is not available."))

    with db.connect(DB_PATH) as conn:
        # Conditional UPDATE rather than read-then-write: two submits racing the
        # same pack must not both start a worker, and the rowcount is how we
        # find out which one won.
        claimed = conn.execute(
            """UPDATE autofill_packs SET status = ?, submitted_at = ?, error_reason = NULL
                WHERE pack_id = ? AND company_id = ? AND status = ?""",
            (STATUS_PROCESSING, _now(), pack_id, company_id, STATUS_DRAFT),
        ).rowcount
    if not claimed:
        raise PackStateError("This pack is already being processed.")

    return {"status": STATUS_PROCESSING}


def _record_file_outcome(pack_id: str, file_id: str, *, run_status: str,
                         run_message: str, review_id: str | None,
                         draft_url: str | None, summary_url: str | None) -> None:
    with db.connect(DB_PATH) as conn:
        conn.execute(
            """UPDATE autofill_pack_files
                  SET run_status = ?, run_message = ?, review_id = ?,
                      draft_url = ?, summary_url = ?, processed_at = ?
                WHERE file_id = ? AND pack_id = ?""",
            (run_status, run_message[:2000], review_id, draft_url, summary_url,
             _now(), file_id, pack_id),
        )


def _primary_document(files, runs_by_path: dict):
    """
    The document the eligibility gate is run against.

    Preference order, and the reason for each: a PDF the classifier accepted as
    a tender (the win-probability model only reads tender PDFs, and a form pack
    carries no tender terms); then any PDF; then the first document. A pack of
    nothing but .docx returnable forms still gets an eligibility verdict — the
    hard gate reads text, not features — with `prediction_available` false and a
    stated reason, which beats no answer.
    """
    pdfs = [f for f in files if (f["file_type"] or "").lower() == "pdf"]
    for f in pdfs:
        run = runs_by_path.get(str(f["storage_path"]))
        cls = getattr(run, "classification", None) if run else None
        if cls is not None and getattr(cls, "is_tender", False):
            return f
    if pdfs:
        return pdfs[0]
    return files[0] if files else None


def process_pack(company_id: str, pack_id: str) -> dict:
    """
    Do the pack's work and leave it in a terminal state. Never raises to the
    caller — a background task that raises leaves a pack in `processing`
    forever, which is the one outcome the spec forbids.

    Everything paid for happens inside `run_autofill_batch`, which checks tier
    and quota before it opens a file and stops the moment the quota is
    exhausted. This function adds the per-file review (`open_review`), the
    eligibility verdict, and the pack-level status.
    """
    from agent_autofill.main_autofill_orchestrator import run_autofill_batch

    files = _pack_files(pack_id)
    profile = get_company_profile(company_id) or {}
    company_name = profile.get("company_name") or ""

    outcome_status = STATUS_ERROR
    error_reason = "Processing did not complete."
    assessment = None
    primary_file_id = None

    try:
        pack_events.emit(pack_id, "pack_started", {"file_count": len(files)})

        if not files:
            error_reason = "The pack has no documents."
            return {"status": outcome_status, "error_reason": error_reason}

        paths = [f["storage_path"] for f in files]
        missing = [f for f in files if not Path(f["storage_path"]).exists()]
        if len(missing) == len(files):
            # /tmp is per-instance. A pack uploaded to one Cloud Functions
            # instance and submitted against another finds nothing on disk, and
            # saying so is far more useful than "0 fields filled".
            error_reason = (
                "None of this pack's uploaded documents could be found on disk. "
                "They may have expired — re-upload them and submit again."
            )
            return {"status": outcome_status, "error_reason": error_reason}

        runs = run_autofill_batch(company_id, paths)
        runs_by_path = {str(r.source_path): r for r in runs}

        drafted = 0
        for f in files:
            run = runs_by_path.get(str(f["storage_path"]))
            if run is None:
                # run_autofill_batch stops early when the quota runs out, so the
                # tail of the pack legitimately has no run. Recorded as such
                # rather than left blank, so files_done still reaches
                # files_total and the pack does not look stalled.
                _record_file_outcome(
                    pack_id, f["file_id"],
                    run_status="not_processed",
                    run_message=("Not processed — the daily Agent Autofill limit was "
                                 "reached earlier in this pack."),
                    review_id=None, draft_url=None, summary_url=None,
                )
                continue

            pack_events.emit(pack_id, "file_opened",
                             {"filename": f["original_filename"]})
            # Token counts come from the run itself — classification is the only
            # paid step, so this is the whole cost of the document.
            # From the classification result itself. An earlier version read
            # run.status ("awaiting review") and a confidence attribute that
            # does not exist, so every document was narrated as
            # "awaiting review (confidence 0.00)" — a sentence that was wrong
            # twice and looked authoritative.
            cls = getattr(run, "classification", None)
            # Before the verdict, because that is the order it happened in: a
            # scan has to be OCR-ed before there is anything to classify. A
            # user watching a scanned document go through deserves to know the
            # text being judged is a machine's reading of an image.
            if cls is not None and getattr(cls, "ocr_used", False):
                pack_events.emit(pack_id, "ocr_used",
                                 {"filename": f["original_filename"]})
                if getattr(cls, "ocr_note", ""):
                    pack_events.emit(pack_id, "ocr_detail",
                                     {"detail": cls.ocr_note})
            if cls is not None:
                pack_events.emit(pack_id, "classified", {
                    "filename": f["original_filename"],
                    "verdict": (getattr(cls, "document_type", None)
                                or ("a tender document" if getattr(cls, "is_tender", False)
                                    else "not a tender document")),
                    "confidence": f"{getattr(cls, 'confidence', 0.0) or 0.0:.2f}",
                }, model_calls=getattr(run, "claude_calls", 0) or 0)

            summary = getattr(run, "extraction_summary", None) or {}
            blanks = summary.get("blank_count") if isinstance(summary, dict) else None
            if blanks is not None:
                pack_events.emit(pack_id, "extracted_summary",
                                 {"filename": f["original_filename"],
                                  "blank_count": blanks})

            review_id = None
            if run.produced_draft and run.fill_result is not None:
                # The per-file review gate, unchanged and unforked. One review
                # per document; the pack is a view over them.
                review = open_review(
                    company_id, run.fill_result,
                    company_name=company_name,
                    summary_path=run.review_summary_path or "",
                )
                review_id = review["review_id"]
                drafted += 1
                pack_events.emit(pack_id, "filled", {
                    "filename": f["original_filename"],
                    "filled_count": len(run.fill_result.filled),
                    "flagged_count": len(run.fill_result.skipped),
                })
                # Both artefacts are served by /api/generated/<name>, which
                # fails closed on an unregistered file. Without this the draft
                # and its summary exist and cannot be downloaded.
                register_generated(Path(run.output_document).name, company_id,
                                   "autofill_pack_draft")
                if run.review_summary_path:
                    register_generated(Path(run.review_summary_path).name, company_id,
                                       "autofill_pack_summary")

            if not (run.produced_draft and run.fill_result is not None):
                # Silence here read as "nothing happened". A PDF has no writer,
                # a legacy .doc is read-only, and a refusal has a reason — all
                # three are worth a sentence rather than a gap in the transcript.
                pack_events.emit(pack_id, "no_draft", {
                    "filename": f["original_filename"],
                    "reason": (run.message or "no draft could be produced")[:200]})

            _record_file_outcome(
                pack_id, f["file_id"],
                run_status=run.status,
                run_message=run.message or "",
                review_id=review_id,
                draft_url=(public_url(Path(run.output_document).name)
                           if run.output_document else None),
                summary_url=run.review_summary_url,
            )

        refused = [r for r in runs if r.status in ("refused_tier", "refused_quota")]
        if drafted == 0 and refused and len(refused) == len(runs):
            error_reason = refused[0].message
            return {"status": outcome_status, "error_reason": error_reason}

        primary = _primary_document(files, runs_by_path)
        if primary is not None and Path(primary["storage_path"]).exists():
            primary_file_id = primary["file_id"]
            try:
                from agent_autofill.integration.tender_assessment import assess_tender

                pack_events.emit(pack_id, "eligibility_run", {})
                assessment = assess_tender(primary["storage_path"],
                                           company_profile=profile)
                # The stored path is ours and uninteresting to a client; the
                # name the user uploaded is what they will recognise.
                if isinstance(assessment, dict):
                    assessment["document"] = primary["original_filename"]
                    pack_events.emit(pack_id, "eligibility_result", {
                        "recommendation": assessment.get("recommendation")
                                          or assessment.get("status") or "unknown"})
                    # A verdict with no reason is not actionable. These are the
                    # gate's own words, not a paraphrase.
                    for reason in (assessment.get("hard_failures") or []):
                        pack_events.emit(pack_id, "eligibility_reason",
                                         {"reason": str(reason)})
                    skipped = assessment.get("prediction_unavailable_reason")
                    if skipped:
                        pack_events.emit(pack_id, "prediction_skipped",
                                         {"reason": str(skipped)})
            except Exception as exc:  # noqa: BLE001
                log.exception("pack %s: eligibility assessment failed", pack_id)
                assessment = {
                    "status": "error",
                    "message": f"Eligibility could not be checked: "
                               f"{type(exc).__name__}: {exc}",
                }

        if drafted == 0:
            # Nothing writable in the pack. Not a crash — a PDF-only pack is
            # analysed and a legacy .doc is reported read-only — but there is
            # no review to complete, so `needs_review` would be a lie.
            reasons = "; ".join(
                f"{f['original_filename']}: {(f['run_message'] or '')[:160]}"
                for f in _pack_files(pack_id)
            )
            pack_events.emit(pack_id, "pack_no_drafts", {})
            error_reason = (
                "No draft could be produced from any document in this pack. " + reasons
            )
            return {"status": outcome_status, "error_reason": error_reason}

        # Said explicitly rather than left as an absence. There is no price
        # lookup — every line item is flagged for a person — and a user who is
        # not told that will assume the blanks mean "nothing found".
        pack_events.emit(pack_id, "pricing_skipped", {})

        # Counted from the same place the detail endpoint counts it. A previous
        # version called a helper that was not in scope, so this always reported
        # 0 while the per-file line above said 15 were outstanding — the two
        # sentences contradicted each other in the same transcript.
        try:
            detail = pack_detail(company_id, pack_id)
            flag_total = int(detail.get("flags", {}).get("outstanding", 0))
            value_total = int(detail.get("values", {}).get("unconfirmed", 0))
        except Exception:  # noqa: BLE001
            # Logged, not swallowed. The first version called a function that
            # did not exist and this except turned that into "0 items need your
            # confirmation" while the same transcript said 15 were outstanding.
            # A count that is wrong reads as authoritative; a count that is
            # missing does not.
            log.exception("pack %s: could not count outstanding items", pack_id)
            flag_total = value_total = 0
        pack_events.emit(pack_id, "review_ready",
                         {"flag_count": flag_total + value_total})

        outcome_status = STATUS_NEEDS_REVIEW
        error_reason = None
        return {"status": outcome_status, "drafted": drafted}

    except Exception as exc:  # noqa: BLE001
        log.exception("pack %s processing failed", pack_id)
        pack_events.emit(pack_id, "pack_failed",
                         {"reason": f"{type(exc).__name__}: {exc}"})
        outcome_status = STATUS_ERROR
        error_reason = f"{type(exc).__name__}: {exc}"
        return {"status": outcome_status, "error_reason": error_reason}

    finally:
        # The whole point of the `finally`: whatever happened above, including a
        # `return` from inside the `try`, the pack leaves `processing`.
        with db.connect(DB_PATH) as conn:
            conn.execute(
                """UPDATE autofill_packs
                      SET status = ?, error_reason = ?, completed_at = ?,
                          assessment_json = ?, primary_file_id = ?
                    WHERE pack_id = ? AND company_id = ?""",
                (outcome_status, error_reason, _now(),
                 json.dumps(assessment) if assessment is not None else None,
                 primary_file_id, pack_id, company_id),
            )


# --- reading ----------------------------------------------------------------


def list_packs(company_id: str) -> list[dict]:
    if not company_id:
        raise PackInputError("A company_id is required — packs are always tenant-scoped.")
    with db.connect(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT p.pack_id, p.pack_name, p.status, p.submitted_at, p.created_at,
                      (SELECT COUNT(*) FROM autofill_pack_files f
                        WHERE f.pack_id = p.pack_id) AS file_count
                 FROM autofill_packs p
                WHERE p.company_id = ?
                ORDER BY p.created_at DESC""",
            (company_id,),
        ).fetchall()
    return [
        {
            "pack_id": r["pack_id"],
            "pack_name": r["pack_name"],
            "file_count": int(r["file_count"] or 0),
            "status": r["status"],
            "submitted_at": r["submitted_at"],
        }
        for r in rows
    ]


def pack_status(company_id: str, pack_id: str) -> dict:
    """
    The polling endpoint's answer. Cheap: two counts and the pack row.

    Read from the database rather than from a process-local dict, so it keeps
    answering after the instance that started the work has gone away.
    """
    pack = _load_pack(company_id, pack_id)
    with db.connect(DB_PATH) as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN processed_at IS NULL THEN 0 ELSE 1 END) AS done
                 FROM autofill_pack_files WHERE pack_id = ?""",
            (pack_id,),
        ).fetchone()
    return {
        "status": pack["status"],
        "files_done": int(row["done"] or 0),
        "files_total": int(row["total"] or 0),
        "error_reason": pack["error_reason"],
    }


def flag_kind(category: str | None, reason: str | None) -> str:
    """
    What KIND of thing a flagged field is, in the fill engine's own words.

    `SkippedField.category` only says "blocked"; which rule blocked it lives in
    the message, and the messages are `never_fill_fields.BLOCK_MESSAGES`. So the
    message is mapped back rather than the label being re-examined here — a
    second classifier would drift from the first one the moment either changed,
    and the one that decided is the one that should be reported.

    Two blocked messages are built at the block site rather than taken from the
    table (an unreadable label, and a field inside the buying institution's own
    section). Those fall through to a plain "blocked", which is accurate.
    """
    if (category or "") != "blocked":
        return category or "unmatched"
    return _MESSAGE_TO_KIND.get((reason or "").strip(), "blocked")


def _review_view(company_id: str, review_id: str) -> dict:
    """
    One document's review, from review_gate, with the flag kind attached.

    Every number here comes from `get_review` / `filled_values`. Nothing is
    recomputed, so a pack cannot disagree with the gate that actually decides.
    """
    review = get_review(company_id, review_id)
    values = filled_values(company_id, review_id)

    items = []
    for item in review["items"]:
        enriched = dict(item)
        enriched["flag_kind"] = flag_kind(item.get("category"), item.get("reason"))
        enriched["acknowledged"] = bool(item.get("acknowledged_at"))
        items.append(enriched)

    # "Are the values confirmed?" is the export gate's question and only the
    # export gate can answer it — the confirmation is a MAC over the values as
    # they stand, so a stored timestamp is not proof. `exportable_as_reviewed`
    # is get_review's own verdict on the flags; the values half is asked of the
    # gate's own helper.
    from agent_autofill.integration.review_gate import _values_unconfirmed

    unconfirmed = _values_unconfirmed(company_id, review_id)

    return {
        "review_id": review_id,
        "document_status": review["document_status"],
        "filled_count": review["filled_count"],
        "flagged_count": review["flagged_count"],
        "acknowledged_count": review["acknowledged_count"],
        "outstanding_count": review["outstanding_count"],
        "advisory_count": review.get("advisory_count", 0),
        "items": items,
        "filled_values": values["values"],
        "values_confirmed": not unconfirmed,
        "unconfirmed_value_count": len(unconfirmed),
        "export_path": review["export_path"],
    }


def pack_detail(company_id: str, pack_id: str) -> dict:
    """
    The pack-level view: one review made of every document's review.

    This is what the frontend renders instead of asking the user to open each
    document's summary in turn. Its whole job is aggregation — every field in it
    is read from review_gate.
    """
    pack = _load_pack(company_id, pack_id)
    files = _pack_files(pack_id)

    file_views = []
    flag_kinds: dict[str, int] = {}
    outstanding: list[dict] = []
    totals = {
        "flags_total": 0,
        "flags_acknowledged": 0,
        "flags_outstanding": 0,
        "advisory_total": 0,
        "values_total": 0,
        "values_unconfirmed": 0,
        "documents_with_review": 0,
    }

    for f in files:
        view = {
            "file_id": f["file_id"],
            "original_filename": f["original_filename"],
            "file_type": f["file_type"],
            "uploaded_at": f["uploaded_at"],
            "status": f["run_status"],
            "message": f["run_message"],
            "review_id": f["review_id"],
            "draft_url": f["draft_url"],
            "review_summary_url": f["summary_url"],
            "review": None,
        }
        if f["review_id"]:
            try:
                review = _review_view(company_id, f["review_id"])
            except ReviewGateError:
                # The review row is gone (a test cleaned it up, or it was
                # deleted). Report the file without it rather than 500 the
                # whole pack.
                review = None
            if review is not None:
                view["review"] = review
                totals["documents_with_review"] += 1
                totals["flags_total"] += review["flagged_count"]
                totals["flags_acknowledged"] += review["acknowledged_count"]
                totals["flags_outstanding"] += review["outstanding_count"]
                totals["advisory_total"] += review.get("advisory_count", 0)
                totals["values_total"] += len(review["filled_values"])
                totals["values_unconfirmed"] += review["unconfirmed_value_count"]
                for item in review["items"]:
                    kind = item["flag_kind"]
                    # Counted over items that actually need a person, so the
                    # breakdown sums to `outstanding` rather than to every row
                    # ever recorded. It used to include the 370 advisory blanks
                    # on a 145-page tender, so the screen said "275 outstanding"
                    # above a list of kinds totalling 645.
                    if item.get("advisory") or item["acknowledged"]:
                        continue
                    flag_kinds[kind] = flag_kinds.get(kind, 0) + 1
                    if True:
                        outstanding.append({
                            "file_id": f["file_id"],
                            "original_filename": f["original_filename"],
                            "review_id": f["review_id"],
                            "item_key": item["item_key"],
                            "label": item["label"],
                            "location": item["location"],
                            "flag_kind": kind,
                            "reason": item["reason"],
                        })
        file_views.append(view)

    status = _recompute_status(company_id, pack_id, pack["status"], totals)

    assessment = None
    if pack["assessment_json"]:
        try:
            assessment = json.loads(pack["assessment_json"])
        except (TypeError, ValueError):
            assessment = None

    return {
        "pack_id": pack["pack_id"],
        "pack_name": pack["pack_name"],
        "status": status,
        "created_at": pack["created_at"],
        "submitted_at": pack["submitted_at"],
        "completed_at": pack["completed_at"],
        "error_reason": pack["error_reason"],
        "file_count": len(files),
        "files_total": len(files),
        "files_done": sum(1 for f in files if f["processed_at"]),
        "primary_file_id": pack["primary_file_id"],
        "assessment": assessment,
        "flags": {
            "total": totals["flags_total"],
            "acknowledged": totals["flags_acknowledged"],
            "outstanding": totals["flags_outstanding"],
            "by_kind": flag_kinds,
        },
        "values": {
            "total": totals["values_total"],
            "unconfirmed": totals["values_unconfirmed"],
            "confirmed": totals["values_total"] - totals["values_unconfirmed"],
        },
        "outstanding": outstanding,
        "files": file_views,
        "exportable": status == STATUS_REVIEWED,
        # Restated on every response, for the same reason autofill_tools does:
        # this string is what a UI or a model reads back before it tells someone
        # the pack is finished.
        "drafts_only": (
            "Everything in this pack is a draft. No signature was applied, no price "
            "was written, and no declaration was answered. Fields marked [ ! ] must "
            "still be completed by hand before anything is submitted."
        ),
    }


def _recompute_status(company_id: str, pack_id: str, current: str,
                      totals: dict) -> str:
    """
    Derive `reviewed` from the reviews themselves, and persist it.

    A pack is reviewed when every flagged field in every document has been
    acknowledged and every pre-filled value has been confirmed — the same two
    conditions `export_reviewed` enforces per document, asked of all of them.
    Derived rather than set: there is no call a client can make that says "this
    pack is reviewed", so there is nothing to forge.

    A pack that has already been exported stays `reviewed`. Editing an
    acknowledgement afterwards invalidates the export (the record and the file
    must keep agreeing), and the export gate is where that is caught.
    """
    if current in (STATUS_DRAFT, STATUS_PROCESSING, STATUS_ERROR):
        return current
    if totals["documents_with_review"] == 0:
        return current

    ready = totals["flags_outstanding"] == 0 and totals["values_unconfirmed"] == 0
    target = STATUS_REVIEWED if ready else STATUS_NEEDS_REVIEW
    if target == current:
        return current

    with db.connect(DB_PATH) as conn:
        conn.execute(
            """UPDATE autofill_packs SET status = ?
                WHERE pack_id = ? AND company_id = ? AND status IN (?, ?)""",
            (target, pack_id, company_id, STATUS_NEEDS_REVIEW, STATUS_REVIEWED),
        )
    return target


# --- review actions ---------------------------------------------------------


def _review_in_pack(company_id: str, pack_id: str, review_id: str) -> None:
    """
    Confirm a review_id actually belongs to this pack.

    `review_gate` already refuses another company's review — company_id is in
    its WHERE clause. This adds the second half: one of my own reviews, from a
    different pack, must not be acknowledgeable through this pack's URL, or the
    pack-level "everything is acknowledged" verdict could be satisfied by work
    done somewhere else.
    """
    _load_pack(company_id, pack_id)
    with db.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT file_id FROM autofill_pack_files WHERE pack_id = ? AND review_id = ?",
            (pack_id, review_id),
        ).fetchone()
    if row is None:
        raise PackNotFound(f"No review {review_id!r} in this pack.")


def acknowledge(company_id: str, pack_id: str, review_id: str, item_key: str,
                note: str, user_id: str = "", username: str = "") -> dict:
    """
    Acknowledge ONE flagged field in one of the pack's documents.

    Deliberately still one field per call. The pack groups documents; it does
    not group acknowledgements. `review_gate.acknowledge_field` rejects "all",
    "*" and a note of "ok", and none of that is relaxed by going through a pack.
    """
    _review_in_pack(company_id, pack_id, review_id)
    result = acknowledge_field(company_id, review_id, item_key, note,
                               user_id=user_id, username=username)
    result["pack_status"] = pack_detail(company_id, pack_id)["status"]
    return result


def confirm_values(company_id: str, pack_id: str, review_id: str,
                   confirmed_keys: list, user_id: str = "",
                   username: str = "") -> dict:
    """Confirm the pre-filled values of ONE document in the pack."""
    _review_in_pack(company_id, pack_id, review_id)
    result = confirm_filled_values(company_id, review_id, confirmed_keys,
                                   user_id=user_id, username=username)
    result["pack_status"] = pack_detail(company_id, pack_id)["status"]
    return result


# --- export -----------------------------------------------------------------


def _outstanding_report(detail: dict) -> dict:
    """What is still in the way, named, for a 409 body."""
    return {
        "status": "error",
        "pack_status": detail["status"],
        "outstanding_flags": detail["flags"]["outstanding"],
        "unconfirmed_values": detail["values"]["unconfirmed"],
        "outstanding": detail["outstanding"],
        "documents_awaiting_value_confirmation": [
            {
                "file_id": f["file_id"],
                "original_filename": f["original_filename"],
                "review_id": f["review_id"],
                "unconfirmed_value_count": f["review"]["unconfirmed_value_count"],
            }
            for f in detail["files"]
            if f["review"] and f["review"]["unconfirmed_value_count"]
        ],
    }


def export_pack(company_id: str, pack_id: str) -> dict:
    """
    Export every reviewed document in the pack.

    Refuses unless the pack is `reviewed`, and the pack is only `reviewed` when
    the per-document conditions are all met — so this is not a new gate, it is
    the existing one asked of every document. `export_reviewed` is then called
    per document and refuses independently: it re-derives outstanding flags
    inside the transaction that flips the status, re-verifies every
    acknowledgement's MAC, and refuses unconfirmed values. If it says no, so
    does this.

    One document returns its own file. Several return a zip, because the
    endpoint promises one `download_url` and handing back the first of six would
    silently lose five. Every member is verified with `verify_export` before it
    goes in — the zip is not itself an export the download route can check, so
    the check happens here, where the files are.
    """
    detail = pack_detail(company_id, pack_id)
    if detail["status"] != STATUS_REVIEWED:
        report = _outstanding_report(detail)
        report["message"] = (
            f"This pack is {detail['status']}, not reviewed. "
            f"{detail['flags']['outstanding']} flagged field(s) still need "
            f"acknowledging and {detail['values']['unconfirmed']} pre-filled "
            f"value(s) still need confirming before it can be exported."
        )
        raise PackStateError(json.dumps(report))

    exports: list[dict] = []
    for f in detail["files"]:
        if not f["review_id"]:
            continue
        result = export_reviewed(company_id, f["review_id"])
        if result.get("status") != "success":
            # The per-document gate refused after the pack thought it was
            # ready — a forged acknowledgement, or state that changed between
            # the two reads. Its message is the useful one; do not paper over it.
            raise PackStateError(json.dumps({
                "status": "error",
                "file_id": f["file_id"],
                "original_filename": f["original_filename"],
                "review_id": f["review_id"],
                "message": result.get("message", "The export gate refused this document."),
                "tamper_detected": result.get("tamper_detected", False),
            }))

        verdict = verify_export(result["export_path"], company_id, f["review_id"])
        if not verdict.get("mac_verified"):
            raise PackStateError(json.dumps({
                "status": "error",
                "file_id": f["file_id"],
                "original_filename": f["original_filename"],
                "message": (
                    "The export was written but does not verify against its own "
                    "review record, so it is not being offered for download. "
                    + str(verdict.get("mac_detail", ""))
                ).strip(),
            }))

        exports.append({
            "file_id": f["file_id"],
            "original_filename": f["original_filename"],
            "review_id": f["review_id"],
            "export_path": result["export_path"],
            "download_url": result["download_url"],
            "review_state": result.get("review_state"),
        })

    if not exports:
        raise PackStateError(json.dumps({
            "status": "error",
            "message": "This pack has no drafted documents to export.",
        }))

    if len(exports) == 1:
        return {
            "download_url": exports[0]["download_url"],
            "documents": exports,
            "pack_id": pack_id,
        }

    bundle_name = f"autofill_pack_{_SAFE_NAME.sub('_', pack_id)[:8]}_REVIEWED.zip"
    bundle = generated_dir() / bundle_name
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in exports:
            archive.write(item["export_path"], Path(item["export_path"]).name)
    # Without this the zip exists and /api/generated refuses it: that route
    # fails closed on a file with no recorded owner.
    register_generated(bundle_name, company_id, "autofill_pack_export")

    return {
        "download_url": public_url(bundle_name),
        "documents": exports,
        "pack_id": pack_id,
    }
