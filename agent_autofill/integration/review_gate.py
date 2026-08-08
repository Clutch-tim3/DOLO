"""
The export gate.

This follows the quotation pattern deliberately — `quote_builder` refuses to
finalise while any line item is MANUAL_REVIEW_REQUIRED, and `finalize_quotation`
is the tool that hits that refusal — but it closes one hole that pattern leaves
open, and the difference is the whole point of the module.

    In the quotation flow the verdict is computed from the caller's argument:

        finalize_quote_flow(quote_id, priced_items)
            -> generate_quote_document({}, priced_items, is_final=True)
            -> has_flags = any(item["price_status"] in [...] for item in priced_items)

    A caller that passes a clean `priced_items` list finalises the quote no
    matter what the database says about it. The gate is real, but the thing it
    inspects is supplied by the party it is meant to constrain.

Here, the verdict comes only from persisted state. `export_reviewed()` takes no
argument that can describe the document's readiness; it re-derives outstanding
flags from the database, inside the same transaction that flips the status, as
a conditional UPDATE:

    UPDATE ... SET status='REVIEWED'
     WHERE review_id=? AND company_id=? AND status='DRAFT'
       AND NOT EXISTS (SELECT 1 FROM autofill_review_item
                        WHERE review_id=? AND acknowledged_at IS NULL)

If `rowcount` is 0 nothing happened and nothing is exported. There is no
parameter to forge and no read-then-write window to race.

Acknowledgement is per field. `acknowledge_field()` takes exactly one
`item_key` and one non-empty note, and the tokens a blanket confirmation would
use ("all", "*", "everything") are rejected by name. Ten flagged fields cost
ten calls, each recorded separately with what the person said about it. That is
the requirement: per-item confirmation, not a single "I agree".

What "reviewed" means here, precisely: a person has looked at every field the
agent refused to fill and said so, one at a time. It does NOT mean the document
is complete — the `[ ! ]` markers are still in it, because a signature, a
declaration and a price are never ours to write. The export banner says exactly
that.
"""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from agent.db_paths import AGENT_MEMORY_DB as DB_PATH
from agent.file_paths import generated_dir, public_url
from agent_autofill.integration.export_metadata import (
    STATUS_REVIEWED,
    STATUS_UNREVIEWED,
    ReviewStamp,
    read_review_state,
    stamp_docx,
)

#: Words a caller would reach for to acknowledge everything at once.
BLANKET_TOKENS = {
    "*", "all", "any", "everything", "every", "each", "-", "none",
    "yes", "ok", "okay", "confirm", "confirmed", "agree", "i agree", "all fields",
}

#: Minimum characters in an acknowledgement note. Short enough not to be busy
#: work, long enough that "ok" does not clear a state-employee declaration.
MIN_NOTE_CHARS = 4


class ReviewGateError(Exception):
    """Raised for input the gate refuses outright rather than answering."""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_review_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS autofill_review (
                review_id     TEXT PRIMARY KEY,
                company_id    TEXT NOT NULL,
                source_path   TEXT,
                draft_path    TEXT,
                summary_path  TEXT,
                company_name  TEXT,
                filled_count  INTEGER NOT NULL DEFAULT 0,
                flagged_count INTEGER NOT NULL DEFAULT 0,
                status        TEXT NOT NULL DEFAULT 'DRAFT',
                created_at    TIMESTAMP,
                reviewed_at   TIMESTAMP,
                export_path   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS autofill_review_item (
                review_id        TEXT NOT NULL,
                item_key         TEXT NOT NULL,
                label            TEXT,
                location         TEXT,
                category         TEXT,
                reason           TEXT,
                acknowledged_at  TIMESTAMP,
                acknowledged_note TEXT,
                PRIMARY KEY (review_id, item_key)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_autofill_review_item_open
            ON autofill_review_item (review_id, acknowledged_at)
        """)


init_review_db()


# --- creation ---------------------------------------------------------------


def open_review(company_id: str, fill_result, company_name: str = "",
                summary_path: str = "") -> dict:
    """
    Record a fill so its flagged fields can be acknowledged one by one.

    `fill_result` is a `fill_engine.document_filler.FillResult`. Every skipped
    field becomes an outstanding item — blocked, low-confidence, no-data and
    unmatched alike. Nothing is pre-acknowledged, including the fields that were
    blocked by design: the user still has to look at a signature line and say
    they have seen it, because that is the field most likely to be missed.

    The draft is stamped UNREVIEWED on the way out, so the file on disk carries
    its state from the moment it exists rather than from the moment it is
    exported.
    """
    if not company_id:
        raise ReviewGateError("A company_id is required to open a review.")

    review_id = str(uuid.uuid4())
    now = datetime.now().isoformat(timespec="seconds")

    items = []
    for i, skipped in enumerate(fill_result.skipped, start=1):
        items.append((
            review_id,
            f"F{i:02d}",
            skipped.label,
            skipped.location,
            skipped.category,
            skipped.reason,
        ))

    with _connect() as conn:
        conn.execute(
            """INSERT INTO autofill_review
               (review_id, company_id, source_path, draft_path, summary_path,
                company_name, filled_count, flagged_count, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?)""",
            (review_id, company_id, fill_result.source_path, fill_result.output_path,
             summary_path, company_name, len(fill_result.filled), len(items), now),
        )
        conn.executemany(
            """INSERT INTO autofill_review_item
               (review_id, item_key, label, location, category, reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            items,
        )

    stamped = None
    draft = Path(fill_result.output_path)
    if draft.suffix.lower() == ".docx" and draft.exists():
        stamped = stamp_docx(draft, ReviewStamp(
            review_id=review_id,
            status=STATUS_UNREVIEWED,
            flags_total=len(items),
            flags_open=len(items),
            filled_count=len(fill_result.filled),
            company_name=company_name,
            source_document=fill_result.source_path,
        ))

    return {
        "status": "success",
        "review_id": review_id,
        "draft_path": fill_result.output_path,
        "summary_path": summary_path,
        "filled_count": len(fill_result.filled),
        "flagged_count": len(items),
        "outstanding": _outstanding_rows(review_id),
        "draft_stamp": stamped,
        "message": (
            f"Draft written. {len(fill_result.filled)} field(s) pre-filled, "
            f"{len(items)} flagged for you. The draft is marked "
            f"{STATUS_UNREVIEWED} inside the file itself and cannot be exported "
            "as reviewed until every flagged field is acknowledged individually."
        ),
    }


# --- reading ----------------------------------------------------------------


def _outstanding_rows(review_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT item_key, label, location, category, reason
                 FROM autofill_review_item
                WHERE review_id = ? AND acknowledged_at IS NULL
                ORDER BY item_key""",
            (review_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _load_review(company_id: str, review_id: str) -> sqlite3.Row:
    """
    Fetch a review, pinned to the calling tenant.

    company_id is part of the WHERE clause rather than checked afterwards, so a
    review_id guessed or lifted from another company's session returns nothing
    at all — the caller cannot even confirm it exists.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM autofill_review WHERE review_id = ? AND company_id = ?",
            (review_id, company_id),
        ).fetchone()
    if row is None:
        raise ReviewGateError(
            f"No autofill review {review_id!r} belongs to this company."
        )
    return row


def get_review(company_id: str, review_id: str) -> dict:
    """The full state of one review: what was filled, what is still open."""
    row = _load_review(company_id, review_id)
    with _connect() as conn:
        items = [dict(r) for r in conn.execute(
            """SELECT item_key, label, location, category, reason,
                      acknowledged_at, acknowledged_note
                 FROM autofill_review_item WHERE review_id = ? ORDER BY item_key""",
            (review_id,),
        ).fetchall()]

    outstanding = [i for i in items if not i["acknowledged_at"]]
    return {
        "status": "success",
        "review_id": review_id,
        "document_status": row["status"],
        "draft_path": row["draft_path"],
        "export_path": row["export_path"],
        "summary_path": row["summary_path"],
        "filled_count": row["filled_count"],
        "flagged_count": row["flagged_count"],
        "acknowledged_count": len(items) - len(outstanding),
        "outstanding_count": len(outstanding),
        "items": items,
        "outstanding": outstanding,
        "exportable_as_reviewed": row["status"] == "DRAFT" and not outstanding,
    }


# --- acknowledgement --------------------------------------------------------


def acknowledge_field(company_id: str, review_id: str, item_key: str,
                      note: str) -> dict:
    """
    Acknowledge exactly ONE flagged field.

    There is no plural form of this function on purpose. A caller that wants to
    clear ten fields makes ten calls and writes ten notes, and each is stored
    against its own field with its own timestamp. That is what makes the export
    banner able to say a person saw each one, rather than that a person clicked
    once.
    """
    _load_review(company_id, review_id)  # tenant pin + existence

    key = (item_key or "").strip()
    if not key:
        raise ReviewGateError("Name the field you are acknowledging, e.g. 'F03'.")
    if key.lower() in BLANKET_TOKENS or "," in key or " " in key:
        raise ReviewGateError(
            f"{item_key!r} is not a single field. Flagged fields are acknowledged "
            "one at a time — call this once per field key (F01, F02, ...). "
            "There is no way to acknowledge them all in one go."
        )

    text = (note or "").strip()
    if len(text) < MIN_NOTE_CHARS or text.lower() in BLANKET_TOKENS:
        raise ReviewGateError(
            "Say what you checked or what you will do about this field. "
            f"A blanket '{note}' is not an acknowledgement of this specific field."
        )

    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        row = conn.execute(
            """SELECT item_key, label, acknowledged_at FROM autofill_review_item
                WHERE review_id = ? AND item_key = ?""",
            (review_id, key),
        ).fetchone()
        if row is None:
            open_keys = [r["item_key"] for r in conn.execute(
                """SELECT item_key FROM autofill_review_item
                    WHERE review_id = ? AND acknowledged_at IS NULL ORDER BY item_key""",
                (review_id,),
            ).fetchall()]
            raise ReviewGateError(
                f"No flagged field {key!r} in this review. "
                f"Still open: {', '.join(open_keys) or 'none'}."
            )
        if row["acknowledged_at"]:
            return {
                "status": "already_acknowledged",
                "item_key": key,
                "label": row["label"],
                "message": f"{key} ({row['label']}) was already acknowledged at {row['acknowledged_at']}.",
                "outstanding": _outstanding_rows(review_id),
            }

        conn.execute(
            """UPDATE autofill_review_item
                  SET acknowledged_at = ?, acknowledged_note = ?
                WHERE review_id = ? AND item_key = ?""",
            (now, text, review_id, key),
        )

    outstanding = _outstanding_rows(review_id)
    return {
        "status": "success",
        "item_key": key,
        "label": row["label"],
        "acknowledged_at": now,
        "outstanding_count": len(outstanding),
        "outstanding": outstanding,
        "message": (
            f"{key} ({row['label']}) acknowledged. "
            + (f"{len(outstanding)} flagged field(s) still to go."
               if outstanding else
               "Every flagged field has now been acknowledged; the document can be "
               "exported as a reviewed draft.")
        ),
    }


# --- export -----------------------------------------------------------------


def _export_name(draft_path: str, review_id: str, suffix: str) -> str:
    stem = Path(draft_path).stem
    # generated_dir() is served by /api/generated/<name>, which only accepts
    # [A-Za-z0-9._-] — anything else 400s before the file is read.
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in stem)[:60]
    return f"{safe}_{suffix}_{review_id[:8]}.docx"


def export_draft(company_id: str, review_id: str) -> dict:
    """
    Export the document as it stands, always marked UNREVIEWED.

    This path is deliberately never blocked. Refusing to hand over a half-done
    draft would push people into emailing the raw file from the working
    directory, which carries no marking at all. Letting them export it, loudly
    stamped, is the safer of the two.
    """
    row = _load_review(company_id, review_id)
    outstanding = _outstanding_rows(review_id)
    draft = Path(row["draft_path"])
    if not draft.exists():
        raise ReviewGateError(f"The draft file for {review_id} is gone: {draft}")

    target = generated_dir() / _export_name(row["draft_path"], review_id, "DRAFT")
    shutil.copy2(draft, target)

    stamp = ReviewStamp(
        review_id=review_id,
        status=STATUS_UNREVIEWED,
        flags_total=row["flagged_count"],
        flags_open=len(outstanding),
        filled_count=row["filled_count"],
        company_name=row["company_name"] or "",
        source_document=row["source_path"] or "",
    )
    written = stamp_docx(target, stamp)

    return {
        "status": "success",
        "review_state": STATUS_UNREVIEWED,
        "export_path": str(target),
        "download_url": public_url(target.name),
        "outstanding_count": len(outstanding),
        "stamp": written,
        "message": (
            f"Exported as an UNREVIEWED draft. {len(outstanding)} flagged field(s) "
            "have not been acknowledged, and the file says so both in its "
            "properties and in a banner on page 1."
        ),
    }


def export_reviewed(company_id: str, review_id: str) -> dict:
    """
    Export the document marked as a reviewed draft.

    Refuses while any flagged field is unacknowledged. The refusal is not a
    check followed by an action — the status change is a conditional UPDATE
    whose WHERE clause contains the check, so there is no argument that can
    describe the document as ready and no window between deciding and acting.

    Returns the quotation module's refusal shape, `{"status": "error",
    "message": ...}`, so a caller that already handles `finalize_quotation`
    handles this identically.
    """
    row = _load_review(company_id, review_id)

    if row["status"] == "REVIEWED" and row["export_path"]:
        return {
            "status": "success",
            "review_state": STATUS_REVIEWED,
            "export_path": row["export_path"],
            "download_url": public_url(Path(row["export_path"]).name),
            "message": "This review was already completed; returning the existing export.",
        }

    draft = Path(row["draft_path"])
    if not draft.exists():
        raise ReviewGateError(f"The draft file for {review_id} is gone: {draft}")

    now = datetime.now().isoformat(timespec="seconds")
    conn = _connect()
    try:
        cur = conn.execute(
            """UPDATE autofill_review
                  SET status = 'REVIEWED', reviewed_at = ?
                WHERE review_id = ?
                  AND company_id = ?
                  AND status = 'DRAFT'
                  AND NOT EXISTS (
                        SELECT 1 FROM autofill_review_item
                         WHERE review_id = ? AND acknowledged_at IS NULL
                  )""",
            (now, review_id, company_id, review_id),
        )

        if cur.rowcount == 0:
            conn.rollback()
            outstanding = _outstanding_rows(review_id)
            listed = "; ".join(
                f"{i['item_key']} {i['label']} ({i['location']})" for i in outstanding
            )
            return {
                "status": "error",
                "review_state": STATUS_UNREVIEWED,
                "outstanding_count": len(outstanding),
                "outstanding": outstanding,
                "message": (
                    f"Cannot export as reviewed: {len(outstanding)} flagged field(s) "
                    f"are unresolved. Acknowledge each one individually first — "
                    f"{listed}."
                ),
            }

        acknowledged = [
            (r["item_key"], r["label"], r["acknowledged_note"])
            for r in conn.execute(
                """SELECT item_key, label, acknowledged_note
                     FROM autofill_review_item WHERE review_id = ? ORDER BY item_key""",
                (review_id,),
            ).fetchall()
        ]

        target = generated_dir() / _export_name(row["draft_path"], review_id, "REVIEWED")
        shutil.copy2(draft, target)

        stamp = ReviewStamp(
            review_id=review_id,
            status=STATUS_REVIEWED,
            flags_total=row["flagged_count"],
            flags_open=0,
            filled_count=row["filled_count"],
            company_name=row["company_name"] or "",
            source_document=row["source_path"] or "",
            acknowledged=acknowledged,
            stamped_at=now,
        )
        written = stamp_docx(target, stamp)

        conn.execute(
            "UPDATE autofill_review SET export_path = ? WHERE review_id = ?",
            (str(target), review_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "status": "success",
        "review_state": STATUS_REVIEWED,
        "export_path": str(target),
        "download_url": public_url(target.name),
        "acknowledged_count": len(acknowledged),
        "stamp": written,
        "message": (
            f"Exported as a reviewed draft. All {row['flagged_count']} flagged "
            "field(s) were acknowledged individually and each acknowledgement is "
            "recorded in the file's properties. Fields marked [ ! ] still need "
            "completing by hand — this is not a submission."
        ),
    }


def verify_export(path: str | Path) -> dict:
    """Read a file's stamp back and say whether it claims to be reviewed."""
    state = read_review_state(path)
    state["path"] = str(path)
    return state


def delete_review(company_id: str, review_id: str) -> dict:
    """Remove a review and its items. Used to clean up test data."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM autofill_review WHERE review_id = ? AND company_id = ?",
            (review_id, company_id),
        )
        if cur.rowcount:
            conn.execute(
                "DELETE FROM autofill_review_item WHERE review_id = ?", (review_id,)
            )
    return {"status": "success", "deleted": bool(cur.rowcount), "review_id": review_id}
