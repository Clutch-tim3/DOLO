"""
What the agent says while a pack is being processed.

The user watches a spinner and a files_done counter today. That tells them
something is happening and nothing about what. This records the real stages as
they occur, so the agent can narrate the way a person would: what it opened,
what it decided, what it filled, what it refused and why.

TWO RULES, AND THE SECOND IS THE IMPORTANT ONE
-----------------------------------------------
1. Events are written by the code that does the work, at the moment it does it.
   Not inferred afterwards from a status column, which is how a progress bar
   ends up describing a stage that never ran.

2. **Nothing here may describe work that does not happen.** The obvious
   temptation is a line like "searching suppliers for prices" — it reads well
   and it would be a lie: no price lookup exists, every line item is flagged
   for a human. `pricing_skipped` says exactly that instead. If a future stage
   is added, add its event beside the code that performs it, never ahead of it.

Stored in the database rather than a process-local list, for the same reason
pack status is: Cloud Functions can restart between the submit and the poll,
and a narration that vanishes mid-sentence is worse than none.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from agent import db
from agent.db_paths import AGENT_MEMORY_DB as DB_PATH

logger = logging.getLogger("agent_autofill.pack_events")

#: Every stage the pipeline can report, with the phrasing the agent uses.
#: `{}` placeholders are filled from the event's detail payload.
#:
#: These correspond one-to-one with real steps in
#: `main_autofill_orchestrator.run_autofill` and `pack_store.process_pack`.
#: There is deliberately no "searching the web" and no "generating a quotation":
#: the first does not exist, and the second is a separate flow the user starts
#: themselves.
STAGE_TEMPLATES = {
    "pack_started":       "Starting on {file_count} document(s) in this pack.",
    "quota_checked":      "Checked your plan — {remaining} of {limit} autofills left today.",
    "file_opened":        "Opening {filename}.",
    "format_detected":    "{filename} is {format}.",
    "classifying":        "Reading {filename} to see whether it is a tender document.",
    "classified":         "{filename}: {verdict} (confidence {confidence}).",
    "extracting":         "Finding the fillable fields in {filename}.",
    "extracted":          "Found {blank_count} blank field(s) in {filename}.",
    "filling":            "Filling what I can from your company profile.",
    "filled":             "{filename}: filled {filled_count}, left {flagged_count} for you.",
    "blocked_summary":    "Refused to fill {count} field(s) in {filename}: {reasons}.",
    "analysis_only":      "{filename} has no fillable form, so I read it for eligibility only.",
    "eligibility_run":    "Checking whether you can bid on this at all.",
    "eligibility_result": "Eligibility: {recommendation}.",
    "pricing_skipped":    "No prices were filled in — every line needs a figure from you.",
    "review_ready":       "Done. {flag_count} item(s) need your confirmation before export.",
    "file_failed":        "Could not process {filename}: {reason}.",
    "pack_failed":        "Stopped: {reason}.",
}


def init_pack_events_db() -> None:
    with db.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS autofill_pack_event (
                event_id    TEXT PRIMARY KEY,
                pack_id     TEXT NOT NULL,
                seq         INTEGER NOT NULL,
                stage       TEXT NOT NULL,
                message     TEXT NOT NULL,
                detail      TEXT,
                tokens_in   INTEGER NOT NULL DEFAULT 0,
                tokens_out  INTEGER NOT NULL DEFAULT 0,
                model_calls INTEGER NOT NULL DEFAULT 0,
                created_at  TIMESTAMP NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pack_event_seq
            ON autofill_pack_event (pack_id, seq)
        """)
        # Additive: a column rather than a key in the detail JSON, so the
        # totals query can sum it. It was in the JSON first and usage always
        # reported zero.
        if "model_calls" not in db.table_columns(conn, "autofill_pack_event"):
            conn.execute("ALTER TABLE autofill_pack_event"
                         " ADD COLUMN model_calls INTEGER NOT NULL DEFAULT 0")


init_pack_events_db()


def _render(stage: str, detail: dict) -> str:
    """
    The sentence for a stage.

    An unknown stage falls back to its own name rather than raising: a missing
    narration line must never be able to fail the work it is describing.
    """
    template = STAGE_TEMPLATES.get(stage)
    if not template:
        return stage.replace("_", " ")
    try:
        return template.format(**detail)
    except (KeyError, IndexError):
        logger.warning("pack event %s missing a template field: %s", stage, detail)
        return stage.replace("_", " ")


def emit(pack_id: str, stage: str, detail: dict | None = None,
         tokens_in: int = 0, tokens_out: int = 0,
         model_calls: int = 0, conn=None) -> None:
    """
    Record one stage.

    `model_calls` is recorded rather than tokens for the classification step:
    ClassificationResult carries is_tender, confidence and document_type but not
    the token counts, which claude_client records internally and never returns.
    Reporting a call count that is true beats reporting a token count that is
    zero because nothing plumbed it through.

    Never raises. A narration failure must not take down a pack that is
    otherwise processing correctly — the work matters, the commentary does not.

    `conn` follows the same rule as `generated_files.register`: pass the open
    connection if the caller holds one, or the second connection deadlocks
    against the first's write lock.
    """
    detail = detail or {}
    try:
        message = _render(stage, detail)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        import uuid

        row = (uuid.uuid4().hex, pack_id, stage, message,
               json.dumps(detail, default=str), int(tokens_in), int(tokens_out),
               int(model_calls), now)

        sql_seq = ("SELECT COALESCE(MAX(seq), 0) + 1 FROM autofill_pack_event"
                   " WHERE pack_id = ?")
        sql_ins = ("INSERT INTO autofill_pack_event"
                   " (event_id, pack_id, seq, stage, message, detail,"
                   "  tokens_in, tokens_out, model_calls, created_at)"
                   " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")

        def _write(c):
            seq = c.execute(sql_seq, (pack_id,)).fetchone()[0]
            c.execute(sql_ins, (row[0], row[1], seq) + row[2:])

        if conn is not None:
            _write(conn)
        else:
            with db.connect(DB_PATH) as own:
                _write(own)
    except Exception:
        logger.exception("could not record pack event %s for %s", stage, pack_id)


def events_since(pack_id: str, after_seq: int = 0) -> list[dict]:
    """
    Everything recorded since `after_seq`, oldest first.

    The client polls with the highest seq it has, so a reconnecting browser
    resumes mid-narration instead of replaying from the beginning.
    """
    with db.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT seq, stage, message, detail, tokens_in, tokens_out,"
            "       model_calls, created_at"
            " FROM autofill_pack_event WHERE pack_id = ? AND seq > ?"
            " ORDER BY seq",
            (pack_id, int(after_seq or 0)),
        ).fetchall()

    out = []
    for r in rows:
        try:
            detail = json.loads(r["detail"]) if r["detail"] else {}
        except (TypeError, ValueError):
            detail = {}
        out.append({
            "seq": r["seq"],
            "stage": r["stage"],
            "message": r["message"],
            "detail": detail,
            "tokens_in": r["tokens_in"],
            "tokens_out": r["tokens_out"],
            "model_calls": r["model_calls"],
            "at": r["created_at"],
        })
    return out


def token_totals(pack_id: str) -> dict:
    """
    What this pack cost.

    `model_calls` is the honest figure available today: classification is the
    only paid step, one call per document. Token counts stay zero until
    ClassificationResult carries them — claude_client records them and does not
    return them — and a zero that is labelled is better than a number that is
    invented.
    """
    with db.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(tokens_in), 0) AS tin,"
            "       COALESCE(SUM(tokens_out), 0) AS tout,"
            "       COALESCE(SUM(model_calls), 0) AS calls"
            " FROM autofill_pack_event WHERE pack_id = ?",
            (pack_id,),
        ).fetchone()
    return {
        "tokens_in": row["tin"] or 0,
        "tokens_out": row["tout"] or 0,
        "model_calls": row["calls"] or 0,
        "tokens_available": False,
    }
