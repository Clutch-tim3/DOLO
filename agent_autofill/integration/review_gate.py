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
                        WHERE review_id=? AND acknowledged_at IS NULL
                          AND advisory=0)

If `rowcount` is 0 nothing happened and nothing is exported. There is no
parameter to forge and no read-then-write window to race.

Acknowledgement is per field. `acknowledge_field()` takes exactly one
`item_key` and one non-empty note, and the tokens a blanket confirmation would
use ("all", "*", "everything") are rejected by name. Ten flagged fields cost
ten calls, each recorded separately with what the person said about it. That is
the requirement: per-item confirmation, not a single "I agree".

What "reviewed" means here, precisely: a person has looked at every field the
agent refused to fill FOR A REASON — a signature, a declaration, a price, a
field it knows but has no value for — and said so, one at a time. Blanks it
could not identify at all are recorded and marked in the document but do not
require a tick; see ADVISORY_CATEGORIES for why. It does NOT mean the document
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

from agent import db
from agent.db_paths import AGENT_MEMORY_DB as DB_PATH
from agent.file_paths import generated_dir, public_url
from agent.generated_files import register as register_generated
from agent_autofill.integration.export_metadata import (
    STATUS_REVIEWED,
    STATUS_UNREVIEWED,
    ReviewStamp,
    read_review_state,
    stamp_docx,
)
from agent_autofill.integration.stamp_signing import (
    ack_mac,
    ack_payload,
    export_payload,
    file_sha256,
    values_payload,
    matches,
    sign,
    stamp_payload,
)

#: Words a caller would reach for to acknowledge everything at once.
BLANKET_TOKENS = {
    "*", "all", "any", "everything", "every", "each", "-", "none",
    "yes", "ok", "okay", "confirm", "confirmed", "agree", "i agree", "all fields",
}

#: Categories recorded but NOT requiring an acknowledgement.
#:
#: `unmatched` is a blank whose label we could not identify. We did not fill it,
#: could not have filled it, and left it visibly marked in the document. Asking
#: a person to tick it is not a safety property — it is noise, and on a real
#: 145-page tender it was 370 of 651 items, which makes the whole gate unusable
#: and trains people to click through the ones that matter.
#:
#: Everything else still requires a person: blocked (signature, declaration,
#: pricing), no_data, low confidence, and every value actually written.
ADVISORY_CATEGORIES = {"unmatched", "unplaceable"}

#: Minimum characters in an acknowledgement note. Short enough not to be busy
#: work, long enough that "ok" does not clear a state-employee declaration.
MIN_NOTE_CHARS = 4


class ReviewGateError(Exception):
    """Raised for input the gate refuses outright rather than answering."""


def _connect():
    """
    Application state, on whichever backend is configured.

    Was sqlite3 directly. agent/db.py keeps the raw SQL — including the
    conditional UPDATE that carries the export gate — working unchanged against
    Cloud SQL, where the data actually survives a cold start.
    """
    return db.connect(DB_PATH)


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
        # Additive migration. Existing rows get NULL, which reads as "not
        # signed" and therefore blocks a reviewed export until each field is
        # acknowledged again through the proper path. That is the intended
        # outcome for acknowledgements made before they were tamper-evident.
        existing = db.table_columns(conn, "autofill_review_item")
        if "ack_mac" not in existing:
            conn.execute("ALTER TABLE autofill_review_item ADD COLUMN ack_mac TEXT")
        # Blanks we never identified and never wrote into. They are recorded so
        # the record is complete and still marked [ ! ] in the document, but
        # they do not require an acknowledgement — see ADVISORY_CATEGORIES.
        if "advisory" not in existing:
            conn.execute("ALTER TABLE autofill_review_item"
                         " ADD COLUMN advisory INTEGER NOT NULL DEFAULT 0")

        # The digest of the exported copy as it stood before stamping. The
        # stamp rewrites the file, so this cannot be recovered from the export
        # itself and has to be kept beside the record for verification.
        review_cols = db.table_columns(conn, "autofill_review")
        if "export_sha256" not in review_cols:
            conn.execute("ALTER TABLE autofill_review ADD COLUMN export_sha256 TEXT")
        # Digest of the FINISHED file plus a MAC over it. Without these the
        # stamp is portable — see export_payload. Existing exports have NULL
        # and stop verifying, which is the correct answer for a file produced
        # before its content was bound.
        if "final_sha256" not in review_cols:
            conn.execute("ALTER TABLE autofill_review ADD COLUMN final_sha256 TEXT")
        if "export_mac" not in review_cols:
            conn.execute("ALTER TABLE autofill_review ADD COLUMN export_mac TEXT")
        # The bulk confirmation of auto-filled values. Until this is set, an
        # export claiming REVIEWED would be asserting a review of values nobody
        # looked at — the gate used to require acknowledgement only of the
        # fields it could NOT fill.
        if "values_confirmed_at" not in review_cols:
            conn.execute(
                "ALTER TABLE autofill_review ADD COLUMN values_confirmed_at TIMESTAMP")
        if "values_confirmed_mac" not in review_cols:
            conn.execute(
                "ALTER TABLE autofill_review ADD COLUMN values_confirmed_mac TEXT")

        # What was actually written into the document. Only filled_count was
        # recorded before, which is a number you cannot show anyone.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS autofill_review_fill (
                review_id  TEXT NOT NULL,
                item_key   TEXT NOT NULL,
                label      TEXT,
                value      TEXT,
                location   TEXT,
                PRIMARY KEY (review_id, item_key)
            )
        """)


init_review_db()


# --- creation ---------------------------------------------------------------


def open_review(company_id: str, fill_result, company_name: str = "",
                summary_path: str = "") -> dict:
    """
    Record a fill so its flagged fields can be acknowledged one by one.

    `fill_result` is a `fill_engine.document_filler.FillResult`. Every skipped
    field is recorded. Those with a REASON — blocked, low-confidence, no-data —
    become outstanding items requiring an individual acknowledgement; nothing is
    pre-acknowledged, including fields blocked by design, because a signature
    line is the one most likely to be skimmed past.

    Blanks in ADVISORY_CATEGORIES are recorded and marked in the document but do
    not require a tick. On a real 145-page PDF tender they were 370 of 651
    items. A gate that demands 651 confirmations is not a stricter gate; it is
    one people learn to click through, including past the 25 signatures.

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
            1 if (skipped.category or "") in ADVISORY_CATEGORIES else 0,
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
               (review_id, item_key, label, location, category, reason, advisory)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            items,
        )
        conn.executemany(
            """INSERT INTO autofill_review_fill
               (review_id, item_key, label, value, location) VALUES (?, ?, ?, ?, ?)""",
            [(review_id, f"V{i:02d}", f.label, str(f.value), getattr(f, "location", ""))
             for i, f in enumerate(fill_result.filled, start=1)],
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
                  AND advisory = 0
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
                      acknowledged_at, acknowledged_note, advisory
                 FROM autofill_review_item WHERE review_id = ? ORDER BY item_key""",
            (review_id,),
        ).fetchall()]

    # Advisory rows are returned — they belong in the record and the UI should
    # be able to list them — but they are not counted as outstanding and do not
    # hold the export gate. This filter has to match the one in
    # `_outstanding_rows` and in the export UPDATE; three places computing
    # "outstanding" differently is how a gate ends up disagreeing with the
    # screen that describes it.
    outstanding = [i for i in items
                   if not i["acknowledged_at"] and not i.get("advisory")]
    advisory = [i for i in items if i.get("advisory")]
    return {
        "status": "success",
        "review_id": review_id,
        "document_status": row["status"],
        "draft_path": row["draft_path"],
        "export_path": row["export_path"],
        "summary_path": row["summary_path"],
        "filled_count": row["filled_count"],
        "flagged_count": row["flagged_count"],
        "acknowledged_count": sum(1 for i in items
                                   if i["acknowledged_at"] and not i.get("advisory")),
        "outstanding_count": len(outstanding),
        "advisory_count": len(advisory),
        "advisory": advisory,
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

        # Signed before the write, so a missing secret fails the
        # acknowledgement rather than recording an unverifiable one.
        mac = ack_mac(review_id, key, now, text)
        conn.execute(
            """UPDATE autofill_review_item
                  SET acknowledged_at = ?, acknowledged_note = ?, ack_mac = ?
                WHERE review_id = ? AND item_key = ?""",
            (now, text, mac, review_id, key),
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
    register_generated(target.name, company_id, "autofill_draft")

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

    # THE FIRST OF THE TWO DEMONSTRATED BYPASSES. Setting acknowledged_at
    # directly in agent_memory.db used to satisfy the UPDATE below, and the
    # export path would then sign the forged state itself. So every
    # acknowledgement is re-verified against its MAC before it counts. A row
    # written by hand has no valid MAC and reads as still outstanding.
    # Tamper detection runs first. "Someone forged an acknowledgement" and
    # "the values are not confirmed yet" are both refusals, but only one is a
    # security signal, and reporting the routine one would bury it.
    forged = _unverifiable_acknowledgements(review_id)
    if forged:
        listed = "; ".join(f"{f['item_key']} ({f['label']})" for f in forged)
        return {
            "status": "error",
            "review_state": STATUS_UNREVIEWED,
            "outstanding_count": len(forged),
            "outstanding": forged,
            "tamper_detected": True,
            "message": (
                f"Cannot export as reviewed: {len(forged)} acknowledgement(s) do not "
                "carry a valid signature, so they were not made through the review "
                f"flow — {listed}. Acknowledge each field again in the app."
            ),
        }

    # The values Agent Autofill WROTE must be confirmed, not just the fields it
    # could not fill. Without this a form with nothing flagged exported as
    # REVIEWED with no human involvement at all, and every ordinary export
    # asserted a review of values nobody had been shown.
    # Only once the flags are clear. While any flagged field is still open,
    # that is the more useful thing to say, and the conditional UPDATE below
    # remains the authority on it — checking flags here as well would just
    # reintroduce a gap between deciding and acting.
    unconfirmed = [] if _outstanding_rows(review_id) else _values_unconfirmed(
        company_id, review_id)
    if unconfirmed:
        return {
            "status": "error",
            "review_state": STATUS_UNREVIEWED,
            "unconfirmed_values": unconfirmed,
            "outstanding_count": len(unconfirmed),
            "message": (
                f"Cannot export as reviewed: {len(unconfirmed)} pre-filled value(s) "
                "have not been confirmed by a person. Show them the filled values "
                "and confirm them together first."
            ),
        }

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
                           AND advisory = 0
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

        ack_times = [
            r["acknowledged_at"]
            for r in conn.execute(
                "SELECT acknowledged_at FROM autofill_review_item WHERE review_id = ?",
                (review_id,),
            ).fetchall()
        ]

        target = generated_dir() / _export_name(row["draft_path"], review_id, "REVIEWED")
        shutil.copy2(draft, target)
        # Inside the open transaction, so it must share the connection.
        register_generated(target.name, company_id, "autofill_reviewed", conn=conn)
        export_sha = file_sha256(target)

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
            # Signed over the draft as copied, before stamping rewrites it.
            mac=sign(stamp_payload(
                company_id=company_id,
                review_id=review_id,
                source_sha256=export_sha,
                status=STATUS_REVIEWED,
                flags_total=row["flagged_count"],
                flags_open=0,
                filled_count=row["filled_count"],
                acknowledged_at_values=ack_times,
                stamped_at=now,
            )),
        )
        written = stamp_docx(target, stamp)

        # Taken after stamping, so it covers the file a recipient actually
        # holds. Signed separately because the stamp MAC lives inside the file
        # and cannot cover the file's own finished bytes.
        final_sha = file_sha256(target)
        conn.execute(
            "UPDATE autofill_review SET export_path = ?, export_sha256 = ?,"
            " final_sha256 = ?, export_mac = ? WHERE review_id = ?",
            (str(target), export_sha, final_sha,
             sign(export_payload(company_id, review_id, final_sha)), review_id),
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


def filled_values(company_id: str, review_id: str) -> dict:
    """
    Every value Agent Autofill wrote into the document, for the person to read
    before they confirm them. This is the list the confirmation is made against.
    """
    _load_review(company_id, review_id)   # tenant check; raises if not theirs
    with _connect() as conn:
        rows = conn.execute(
            """SELECT item_key, label, value, location FROM autofill_review_fill
                WHERE review_id = ? ORDER BY item_key""",
            (review_id,),
        ).fetchall()
    return {
        "status": "success",
        "review_id": review_id,
        "values": [dict(r) for r in rows],
        "count": len(rows),
    }


def confirm_filled_values(company_id: str, review_id: str,
                          confirmed_keys: list = None) -> dict:
    """
    Confirm, in one step, every value Agent Autofill wrote into the document.

    The gate used to require acknowledgement only of the fields it could NOT
    fill. That left the values it DID write unconfirmed by anyone — and on a
    form with no flagged fields at all, a REVIEWED export needed no human
    involvement whatsoever. This closes that.

    `confirmed_keys` is the set of item keys the person was shown. It must
    match the stored set exactly: a partial list is a partial review, and a
    list containing a key that is not there means the caller is confirming
    something other than what this document contains. Both are refused rather
    than intersected, because silently confirming the overlap is how a
    confirmation stops meaning anything.
    """
    _load_review(company_id, review_id)

    with _connect() as conn:
        rows = conn.execute(
            "SELECT item_key, label, value FROM autofill_review_fill"
            " WHERE review_id = ? ORDER BY item_key",
            (review_id,),
        ).fetchall()

    stored = {r["item_key"] for r in rows}
    if confirmed_keys is None:
        return {"status": "error", "message":
                "Pass the item keys of the values you displayed, so the "
                "confirmation records what was actually seen."}
    supplied = {str(k) for k in confirmed_keys}
    if supplied != stored:
        missing, extra = sorted(stored - supplied), sorted(supplied - stored)
        return {
            "status": "error",
            "message": (
                "The confirmed set does not match the values in this document. "
                + (f"Not confirmed: {', '.join(missing)}. " if missing else "")
                + (f"Not in this document: {', '.join(extra)}." if extra else "")
            ),
            "missing": missing,
            "unknown": extra,
        }

    now = datetime.now().isoformat(timespec="seconds")
    pairs = [(r["item_key"], r["label"], r["value"]) for r in rows]
    mac = sign(values_payload(company_id, review_id, pairs, now))
    with _connect() as conn:
        conn.execute(
            "UPDATE autofill_review SET values_confirmed_at = ?,"
            " values_confirmed_mac = ? WHERE review_id = ? AND company_id = ?"
            " AND status = 'DRAFT'",
            (now, mac, review_id, company_id),
        )
    return {"status": "success", "confirmed_count": len(pairs),
            "confirmed_at": now,
            "message": f"{len(pairs)} pre-filled value(s) confirmed."}


def _values_unconfirmed(company_id: str, review_id: str) -> list[dict]:
    """
    Empty when the auto-filled values carry a valid bulk confirmation.

    Re-derives the MAC from the values as they stand now, so editing one after
    confirmation invalidates it — the confirmation covers what the person saw.
    A review that filled nothing has nothing to confirm.
    """
    with _connect() as conn:
        rec = conn.execute(
            "SELECT values_confirmed_at, values_confirmed_mac FROM autofill_review"
            " WHERE review_id = ? AND company_id = ?",
            (review_id, company_id),
        ).fetchone()
        rows = conn.execute(
            "SELECT item_key, label, value FROM autofill_review_fill"
            " WHERE review_id = ? ORDER BY item_key",
            (review_id,),
        ).fetchall()

    if not rows:
        return []
    if rec is None or not rec["values_confirmed_at"]:
        return [dict(r) for r in rows]

    pairs = [(r["item_key"], r["label"], r["value"]) for r in rows]
    if not matches(rec["values_confirmed_mac"],
                   values_payload(company_id, review_id, pairs,
                                  rec["values_confirmed_at"])):
        return [dict(r) for r in rows]
    return []


def verify_export_by_path(path: str | Path) -> dict | None:
    """
    Verify a file without being told which review it belongs to.

    The download route serves files by name and has no review context, so it
    could not call `verify_export` — which is why that function sat with no
    caller outside tests while the residual it exists to catch stayed live.
    This closes the gap by looking the review up from the file itself.

    Returns None when the file is not a known Agent Autofill export, so
    quotation PDFs and accreditation reports going through the same route are
    passed through untouched rather than being judged by a gate that does not
    apply to them.
    """
    name = Path(path).name
    with _connect() as conn:
        row = conn.execute(
            """SELECT company_id, review_id FROM autofill_review
                WHERE export_path IS NOT NULL AND export_path LIKE ?
                ORDER BY reviewed_at DESC LIMIT 1""",
            (f"%{name}",),
        ).fetchone()
    if row is None:
        return None
    return verify_export(path, row["company_id"], row["review_id"])


def _unverifiable_acknowledgements(review_id: str) -> list[dict]:
    """
    Acknowledgements whose stored MAC does not match their contents.

    Recomputing per row rather than trusting a flag: the MAC covers
    `acknowledged_at` and the note, so editing either — or inventing a row
    wholesale — lands here. A row acknowledged before signing existed has a
    NULL mac, which also lands here, by design.
    """
    with _connect() as conn:
        rows = conn.execute(
            """SELECT item_key, label, location, acknowledged_at,
                      acknowledged_note, ack_mac
                 FROM autofill_review_item
                WHERE review_id = ? AND acknowledged_at IS NOT NULL
                ORDER BY item_key""",
            (review_id,),
        ).fetchall()

    bad = []
    for r in rows:
        payload_ok = matches(
            r["ack_mac"],
            ack_payload(review_id, r["item_key"], r["acknowledged_at"],
                        r["acknowledged_note"] or ""),
        )
        if not payload_ok:
            bad.append({
                "item_key": r["item_key"],
                "label": r["label"],
                "location": r["location"],
                "reason": "acknowledgement signature invalid or missing",
            })
    return bad


def verify_export(path: str | Path, company_id: str = None,
                  review_id: str = None) -> dict:
    """
    Read a file's stamp back and say whether it claims to be reviewed.

    With `company_id` and `review_id` this also recomputes the stamp MAC from
    the review record and reports whether the file's signature matches. That is
    the check that catches a stamp built outside `export_reviewed()` — the
    forger can write any counts they like into the document, but not a MAC that
    verifies against the record they are claiming.

    Without them the answer is limited to what the file says about itself, and
    `mac_verified` is None rather than False, because "not checked" and "failed
    the check" are different findings.
    """
    state = read_review_state(path)
    state["path"] = str(path)
    state["mac_verified"] = None

    if company_id is None or review_id is None:
        return state

    with _connect() as conn:
        rec = conn.execute(
            """SELECT flagged_count, filled_count, export_sha256, export_path,
                      final_sha256, export_mac
                 FROM autofill_review WHERE review_id = ? AND company_id = ?""",
            (review_id, company_id),
        ).fetchone()
        ack_times = [
            r["acknowledged_at"]
            for r in conn.execute(
                "SELECT acknowledged_at FROM autofill_review_item WHERE review_id = ?",
                (review_id,),
            ).fetchall()
        ]

    if rec is None:
        state["mac_verified"] = False
        state["mac_detail"] = "No such review for this company."
        return state

    # Any acknowledgement that fails its own MAC invalidates the export
    # regardless of what the stamp says, because the stamp was signed over
    # timestamps that have since been rewritten.
    forged = _unverifiable_acknowledgements(review_id)

    # Does this file's content match what was exported? Without this the stamp
    # is portable: the body of a genuine export can be rewritten, or the whole
    # stamp lifted onto an unrelated document, and everything below still
    # passes — the stamp MAC only ties the stamp to the record, not to these
    # bytes. Checked against a MAC so that editing the stored digest to match a
    # tampered file fails too.
    content_bound = matches(
        rec["export_mac"],
        export_payload(company_id, review_id, file_sha256(path)),
    )

    state["content_verified"] = content_bound
    state["mac_verified"] = content_bound and (not forged) and matches(
        state.get("mac"),
        stamp_payload(
            company_id=company_id,
            review_id=review_id,
            source_sha256=rec["export_sha256"] or "",
            status=state.get("content_status") or "",
            flags_total=state.get("flags_total") or 0,
            flags_open=state.get("flags_open") or 0,
            filled_count=state.get("filled") or 0,
            acknowledged_at_values=ack_times,
            stamped_at=state.get("stamped_at") or "",
        ),
    )
    if not content_bound:
        state["mac_detail"] = (
            "This file's contents do not match the export that was recorded "
            "for this review — it has been altered, or the stamp was copied "
            "from a different document."
        )
    elif forged:
        state["mac_detail"] = (
            f"{len(forged)} acknowledgement(s) fail their own signature."
        )
    elif not state["mac_verified"]:
        state["mac_detail"] = (
            "The stamp's signature does not match this review record."
        )
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
