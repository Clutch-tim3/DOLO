"""
Canonical company memory.

`get_company_profile` is the single source of truth for company facts. Nothing
else may hold a second copy: in particular, company facts must never be read
back out of an LLM's conversational context, because the model will happily
reconstruct a plausible registration number it saw once three turns ago. Read
the row.

WRITES ARE GATED. `update_company_profile` refuses any write that does not carry
`confirmed=True` and instead returns the diff it would have applied. See the
long comment on that function for why.
"""

import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path

from agent.db_paths import AGENT_MEMORY_DB as DB_PATH

# Every column a write may target. The first block is the original set; the
# second is the Agent Autofill extension. Anything not in here is silently
# ignored by update_company_profile, which is what stops a model from inventing
# a column name.
PROFILE_WRITABLE_FIELDS = [
    "company_name",
    "registration_number",
    "csd_number",
    "bbbee_level",
    "province",
    "registered_municipality",
    "industry",
    "logo_file_path",
    # --- Agent Autofill ---
    "directors",
    "postal_address",
    "physical_address",
    "tax_reference_number",
    "vat_registration_number",
    "standard_contact_person",
    "standard_phone",
    "standard_cell",
    "standard_fax",
    "standard_email",
    "authorized_signatory_name",
    "authorized_signatory_capacity",
    "tax_compliance_pin",
]

# Columns added after the original schema shipped. Applied with ALTER TABLE ...
# ADD COLUMN, which SQLite performs in place: existing rows keep every value they
# had and receive NULL in the new column. No table rebuild, no data copy, no
# DROP. That is what makes this migration non-destructive by construction rather
# than by testing.
_PROFILE_MIGRATIONS = [
    ("directors", "TEXT"),
    ("postal_address", "TEXT"),
    ("physical_address", "TEXT"),
    ("tax_reference_number", "TEXT"),
    ("vat_registration_number", "TEXT"),
    ("standard_contact_person", "TEXT"),
    ("standard_phone", "TEXT"),
    ("standard_cell", "TEXT"),
    ("standard_fax", "TEXT"),
    ("standard_email", "TEXT"),
    ("authorized_signatory_name", "TEXT"),
    ("authorized_signatory_capacity", "TEXT"),
    ("tax_compliance_pin", "TEXT"),
]

# Fields stored as JSON text but handed to callers as Python objects.
_JSON_FIELDS = {"directors"}


# =============================================================================
# SIGNATURE BOUNDARY
# =============================================================================
# `authorized_signatory_name` is a NAME ONLY -- plain text, nothing else.
#
# It MUST NEVER be used to auto-apply a signature image, a scanned mark, an
# initial, a drawn squiggle, a script-font rendering of the name, or any other
# artefact that represents a person having signed a document. No signature image
# may ever be stored in this database, in any column, in any table.
#
# The reason is not tidiness. Agent Autofill drafts real South African
# government bid documents. Applying a signature that the signatory did not
# personally apply to that specific document is forgery; on a state tender it is
# fraud against the state, and it voids the bid. The human signs the final
# document, every time, themselves.
#
# The guard below exists so that this comment is enforced rather than merely
# believed. It runs on every write path into the profile.
# =============================================================================

_SIGNATURE_ASSET_KEY_MARKERS = (
    "signature_image",
    "signature_file",
    "signature_path",
    "signature_data",
    "signature_base64",
    "signature_blob",
    "signature_png",
    "signature_jpg",
    "signature_svg",
    "signature_mark",
    "signature_bitmap",
    "specimen_signature",
    "esignature",
    "e_signature",
    "digital_signature",
    "signature_stamp",
    "initials_image",
)

_SIGNATURE_VALUE_MARKERS = (
    "data:image",
    "data:application/pdf",
)

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".tif", ".tiff")


class SignatureAssetRefused(Exception):
    """Raised when a write tries to store anything resembling a signature mark."""


def assert_no_signature_asset(fields: dict) -> None:
    """
    Refuse any attempt to store a signature artefact.

    Two checks, because the two realistic mistakes look different:

      1. A new key that smells like a signature asset (`signature_image`,
         `esignature`, ...). Those are not writable columns anyway, so they
         would be dropped silently -- and a silent drop is how a bad idea
         survives to be retried against a column that does exist.

      2. An image or data-URI *value* landing in `authorized_signatory_name`.
         That is the specific failure this boundary exists to stop: the field
         quietly graduating from "name" to "the thing we stamp on the form".
    """
    if not fields:
        return

    for key, value in fields.items():
        lowered = str(key).strip().lower()
        if any(marker in lowered for marker in _SIGNATURE_ASSET_KEY_MARKERS):
            raise SignatureAssetRefused(
                f"Refused to store '{key}'. Signature images, scans and marks are "
                "never stored. authorized_signatory_name holds a name only; the "
                "signatory signs the final document themselves."
            )

        if key == "authorized_signatory_name" and value is not None:
            text = str(value).strip()
            low = text.lower()
            if any(marker in low for marker in _SIGNATURE_VALUE_MARKERS):
                raise SignatureAssetRefused(
                    "Refused: authorized_signatory_name received embedded file data. "
                    "This field is a NAME ONLY and must never carry a signature image."
                )
            if low.endswith(_IMAGE_SUFFIXES):
                raise SignatureAssetRefused(
                    f"Refused: authorized_signatory_name received a file path ({text!r}). "
                    "This field is a NAME ONLY and must never carry a signature image."
                )


# =============================================================================


def _migrate_company_profile(conn) -> list:
    """
    Additively bring an existing company_profile table up to date.

    Returns the list of columns actually added, so callers (and tests) can see
    what happened. Every operation is ALTER TABLE ... ADD COLUMN: SQLite appends
    the column and back-fills NULL. Existing rows are never rewritten, so
    nothing that was stored before can be lost here.
    """
    cur = conn.cursor()
    existing = {row[1] for row in cur.execute("PRAGMA table_info(company_profile)")}
    if not existing:
        # Table does not exist yet; schema.sql will create it complete.
        return []

    added = []
    for column, coltype in _PROFILE_MIGRATIONS:
        if column not in existing:
            cur.execute(f"ALTER TABLE company_profile ADD COLUMN {column} {coltype}")
            added.append(column)
    return added


def init_db():
    schema_path = Path(__file__).parent / "schema.sql"
    with sqlite3.connect(DB_PATH) as conn:
        with open(schema_path, "r") as f:
            conn.executescript(f.read())
        # schema.sql's CREATE TABLE IF NOT EXISTS is a no-op against a database
        # that already has the table, so a pre-existing install would never see
        # the new columns without this.
        _migrate_company_profile(conn)
        conn.commit()


def _decode_profile(row: dict) -> dict:
    for field in _JSON_FIELDS:
        raw = row.get(field)
        if isinstance(raw, str) and raw.strip():
            try:
                row[field] = json.loads(raw)
            except (ValueError, TypeError):
                # Leave the raw text in place rather than throwing away data we
                # cannot parse. A caller seeing a string knows it is malformed.
                pass
        elif raw is None:
            row[field] = None
    return row


def get_company_profile(company_id: str) -> dict:
    """
    THE single source of truth for company facts.

    Never substitute values remembered from conversation for this call.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM company_profile WHERE company_id = ?", (company_id,))
        row = cur.fetchone()
        return _decode_profile(dict(row)) if row else {}


def _encode_for_storage(key, value):
    if key in _JSON_FIELDS and not isinstance(value, (str, type(None))):
        return json.dumps(value)
    return value


def _normalise_for_diff(key, value):
    """
    Put a proposed value into the same shape `get_company_profile` returns, so
    the two can be compared.

    Only the JSON-backed columns are decoded. Decoding every string instead was
    a real bug: `json.loads("4880156723")` is the integer 4880156723, so a VAT
    number re-submitted unchanged compared unequal to the stored string and the
    profile reported a phantom "4880156723 -> 4880156723" change on every save.
    That trains a user to click through confirmation dialogs, which is the
    opposite of what the confirmation gate is for.
    """
    if key in _JSON_FIELDS and isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def preview_company_profile_update(company_id: str, fields: dict) -> dict:
    """
    What `update_company_profile` *would* change, without changing anything.

    This is what the agent shows the user in order to obtain the confirmation
    that the write then requires.
    """
    assert_no_signature_asset(fields)

    current = get_company_profile(company_id)
    changes, ignored = [], []
    for key, value in (fields or {}).items():
        if key not in PROFILE_WRITABLE_FIELDS:
            ignored.append(key)
            continue
        before = current.get(key)
        after = _normalise_for_diff(key, value)
        if before != after:
            changes.append({"field": key, "current": before, "proposed": after})

    return {
        "company_id": company_id,
        "profile_exists": bool(current),
        "changes": changes,
        "ignored_fields": ignored,
        "no_op": not changes,
    }


def update_company_profile(company_id: str, fields: dict, confirmed: bool = False) -> dict:
    """
    Write company facts. REFUSES unless `confirmed=True`.

    WHY THE GATE IS CODE AND NOT A PROMPT
    -------------------------------------
    The tool description used to say "REQUIRES explicit user confirmation before
    any write". That is a request addressed to a language model, and a language
    model under pressure to be helpful will satisfy it by deciding, on its own,
    that the user confirmed. There was nothing between an inferred fact and a
    committed row.

    These rows auto-fill real South African government tender documents. A VAT
    number the model half-remembered from a PDF, written silently, is submitted
    to an organ of state under the bidder's name.

    So the default is refusal. An unconfirmed call performs no INSERT and no
    UPDATE -- not even the "create the row if missing" INSERT -- and returns the
    diff instead, for the agent to put in front of the user. Only a second call
    carrying `confirmed=True`, made after the user actually answered, writes.

    `confirmed=True` asserts that a human was shown these specific values and
    approved them. It is not a formality to pass by default, and it must never
    be hard-coded in a caller that has not shown the user anything.
    """
    if not fields:
        return {"status": "error", "message": "No fields provided", "written": False}

    try:
        assert_no_signature_asset(fields)
    except SignatureAssetRefused as e:
        return {"status": "refused", "reason": "signature_asset", "message": str(e), "written": False}

    preview = preview_company_profile_update(company_id, fields)

    if not confirmed:
        # No write of any kind happens on this branch.
        return {
            "status": "confirmation_required",
            "written": False,
            "company_id": company_id,
            "pending_changes": preview["changes"],
            "ignored_fields": preview["ignored_fields"],
            "message": (
                "Nothing was saved. Show the user exactly these values, ask them to "
                "confirm, and only then call update_company_profile again with "
                "confirmed=true. Do not confirm on the user's behalf."
            ),
        }

    if preview["no_op"]:
        return {
            "status": "success",
            "written": False,
            "updated_fields": [],
            "message": "Stored values already match; nothing to write.",
        }

    changed_fields = [c["field"] for c in preview["changes"]]

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT company_id FROM company_profile WHERE company_id = ?", (company_id,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO company_profile (company_id, created_at, updated_at) VALUES (?, ?, ?)",
                (company_id, datetime.now(), datetime.now()),
            )

        updates, values = [], []
        for k in changed_fields:
            updates.append(f"{k} = ?")
            values.append(_encode_for_storage(k, fields[k]))

        updates.append("updated_at = ?")
        values.append(datetime.now())
        values.append(company_id)

        query = f"UPDATE company_profile SET {', '.join(updates)} WHERE company_id = ?"
        cur.execute(query, values)
        conn.commit()

    return {
        "status": "success",
        "written": True,
        "updated_fields": changed_fields,
        "ignored_fields": preview["ignored_fields"],
    }


def delete_company_profile(company_id: str) -> dict:
    """
    Remove a profile row entirely. Exists for test-fixture teardown so tests do
    not leave rows behind in a database that holds real company data.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM company_profile WHERE company_id = ?", (company_id,))
        deleted = cur.rowcount
        conn.commit()
    return {"status": "success", "deleted_rows": deleted}


def get_company_documents(company_id: str) -> list:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM company_documents WHERE company_id = ?", (company_id,))
        rows = cur.fetchall()

        docs = []
        for r in rows:
            d = dict(r)
            if d.get("parsed_fields"):
                d["parsed_fields"] = json.loads(d["parsed_fields"])
            docs.append(d)
        return docs


def add_company_document(company_id: str, document_type: str, file_path: str, parsed_fields: dict = None, expiry_date: str = None) -> str:
    doc_id = str(uuid.uuid4())
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        parsed_json = json.dumps(parsed_fields) if parsed_fields else None
        cur.execute(
            """INSERT INTO company_documents (id, company_id, document_type, file_path, expiry_date, parsed_fields, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, company_id, document_type, file_path, expiry_date, parsed_json, datetime.now())
        )
        conn.commit()
    return doc_id


def log_conversation(company_id: str, user_message: str, agent_response: str, tool_calls_made: list = None):
    log_id = str(uuid.uuid4())
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        tools_json = json.dumps(tool_calls_made) if tool_calls_made else None
        cur.execute(
            """INSERT INTO conversation_log (id, company_id, user_message, agent_response, tool_calls_made, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (log_id, company_id, user_message, agent_response, tools_json, datetime.now())
        )
        conn.commit()


def search_conversation_history(company_id: str, query: str = None, limit: int = 5) -> list:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if query:
            cur.execute("""
                SELECT * FROM conversation_log
                WHERE company_id = ? AND (user_message LIKE ? OR agent_response LIKE ?)
                ORDER BY timestamp DESC LIMIT ?
            """, (company_id, f"%{query}%", f"%{query}%", limit))
        else:
            cur.execute("SELECT * FROM conversation_log WHERE company_id = ? ORDER BY timestamp DESC LIMIT ?", (company_id, limit))

        rows = cur.fetchall()
        logs = []
        for r in rows:
            d = dict(r)
            if d.get("tool_calls_made"):
                d["tool_calls_made"] = json.loads(d["tool_calls_made"])
            logs.append(d)
        return logs


# Initialize schema
init_db()
