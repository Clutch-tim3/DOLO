"""
What the user has already explained about a label, reused on the next tender.

P1-5. The owner: "sometimes it even doesn't understand the field so I need this
to learn through every tender."

Every pack starts from zero. The same SBD 6.1 table is misread the same way
next month, and a label a person has already explained once is explained again.
On his pack, 205 labels are real questions the dictionary does not know —
"Description of contract", "Date contract started", "Name of State institution".

WHAT IS LEARNED, AND WHAT IS NOT

Two things, both decisions a person actually made:

    mapping     "this label means bbbee_level"
    not_a_field "this is not something to fill in"

Nothing is learned by observation. A fill that was not corrected is not
evidence the fill was right — the user may simply not have looked, and treating
silence as approval is how a system teaches itself a mistake.

A LESSON RAISES CONFIDENCE. IT NEVER OPENS A DOOR.

This is the constraint that matters, and it is enforced in `apply_learning`
rather than described here: a learned mapping is consulted only when the
dictionary has no confident answer, and the result is still passed through
`never_fill_fields.is_blocked` and `SAFE_FILL_FIELDS` exactly as a dictionary
match would be. Nothing learned can cause a signature, a price or a sworn
declaration to be filled, because nothing learned reaches those decisions —
they are made downstream, on the canonical field, by code this module cannot
influence.

Learning a bad mapping therefore costs a wrong value in a fillable field, which
is visible in the review and correctable. It cannot cost a signature.

WHY THE USER IS TOLD

A decision that came from a previous tender is reported as such, so a wrong
lesson can be corrected rather than silently repeated. A system that learns
invisibly is one nobody can argue with.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from agent import db
from agent.db_paths import AGENT_MEMORY_DB as DB_PATH

KIND_MAPPING = "mapping"
KIND_NOT_A_FIELD = "not_a_field"

#: How many times a lesson must be taught before it is applied. One is enough:
#: it is a person answering a direct question about a specific label, not an
#: inference from behaviour.
MIN_OCCURRENCES = 1

_schema_ready: set = set()


def normalise(label: str) -> str:
    """
    The key a lesson is stored against.

    Case, punctuation and whitespace vary between packs for what is plainly the
    same question — "E-MAIL ADDRESS", "E-mail address:", "Email Address" — so
    the key is flattened. Digits are kept: "Director 1" and "Director 2" are
    different rows of a table and must not collapse into one lesson.
    """
    text = (label or "").strip().lower()
    # Hyphens and slashes JOIN rather than separate: "E-MAIL" and "EMAIL" are
    # the same word, and splitting on the hyphen made them different keys.
    # Same for "B-BBEE" / "BBBEE" and "CO-OPERATIVE" / "COOPERATIVE".
    text = re.sub(r"[-/–—]+", "", text)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _ensure_schema(conn) -> None:
    pid = os.getpid()
    if pid in _schema_ready:
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS autofill_label_lesson (
            company_id      TEXT NOT NULL,
            normalised      TEXT NOT NULL,
            kind            TEXT NOT NULL,
            canonical_field TEXT,
            example_label   TEXT,
            taught_by       TEXT,
            taught_at       TEXT NOT NULL,
            occurrences     INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (company_id, normalised)
        )
    """)
    conn.commit()
    _schema_ready.add(pid)


def teach(company_id: str, label: str, *, canonical_field: str = None,
          not_a_field: bool = False, taught_by: str = "") -> dict:
    """
    Record what a person said a label means.

    Scoped to a company. One supplier's forms and their vocabulary are their
    own, and a lesson learned from one customer's tender must not change how
    another customer's form is read — that is the same tenancy rule as
    everywhere else, applied to something less obviously personal.
    """
    key = normalise(label)
    if not key:
        raise ValueError("a label is required")
    if not company_id:
        raise ValueError("company_id is required")

    if not_a_field:
        kind, field = KIND_NOT_A_FIELD, None
    else:
        field = (canonical_field or "").strip()
        if not field:
            raise ValueError("either canonical_field or not_a_field is required")
        kind = KIND_MAPPING

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        existing = conn.execute(
            "SELECT occurrences FROM autofill_label_lesson"
            " WHERE company_id = ? AND normalised = ?", (company_id, key)).fetchone()

        if existing:
            conn.execute(
                "UPDATE autofill_label_lesson SET kind = ?, canonical_field = ?,"
                " example_label = ?, taught_by = ?, taught_at = ?,"
                " occurrences = occurrences + 1"
                " WHERE company_id = ? AND normalised = ?",
                (kind, field, label, taught_by or "", now, company_id, key))
        else:
            conn.execute(
                "INSERT INTO autofill_label_lesson (company_id, normalised, kind,"
                " canonical_field, example_label, taught_by, taught_at, occurrences)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                (company_id, key, kind, field, label, taught_by or "", now))
        conn.commit()

    return {"status": "success", "normalised": key, "kind": kind,
            "canonical_field": field}


def lookup(company_id: str, label: str) -> dict | None:
    """What this company has already been told about this label, or None."""
    key = normalise(label)
    if not key or not company_id:
        return None

    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM autofill_label_lesson"
            " WHERE company_id = ? AND normalised = ?", (company_id, key)).fetchone()

    if row is None or row["occurrences"] < MIN_OCCURRENCES:
        return None

    return {
        "normalised": row["normalised"],
        "kind": row["kind"],
        "canonical_field": row["canonical_field"],
        "example_label": row["example_label"],
        "taught_at": row["taught_at"],
        "taught_by": row["taught_by"],
        "occurrences": row["occurrences"],
        # Surfaced so the UI and the agent can say where this came from. A
        # system that learns invisibly is one nobody can argue with.
        "source": "learned from a previous tender",
    }


def forget(company_id: str, label: str) -> bool:
    """Unlearn a lesson. True if one went."""
    key = normalise(label)
    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        existed = conn.execute(
            "SELECT 1 FROM autofill_label_lesson WHERE company_id = ? AND normalised = ?",
            (company_id, key)).fetchone()
        conn.execute(
            "DELETE FROM autofill_label_lesson WHERE company_id = ? AND normalised = ?",
            (company_id, key))
        conn.commit()
    return bool(existed)


def lessons(company_id: str) -> list:
    """Everything this company has taught, so it can be reviewed and corrected."""
    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM autofill_label_lesson WHERE company_id = ?"
            " ORDER BY taught_at DESC", (company_id,)).fetchall()
    return [{
        "normalised": r["normalised"], "kind": r["kind"],
        "canonical_field": r["canonical_field"], "example_label": r["example_label"],
        "taught_at": r["taught_at"], "occurrences": r["occurrences"],
    } for r in rows]


def apply_learning(company_id: str, label: str, match) -> tuple:
    """
    Let a lesson answer a label the dictionary could not.

    Returns (canonical_field_or_None, source). `match` is the dictionary's
    AliasMatch, or None.

    THE ORDER IS THE SAFETY PROPERTY. A confident dictionary match always wins:
    lessons fill gaps, they do not override the shared vocabulary, so one
    company cannot teach itself that "Signature" means "company_name".

    The returned field goes on to `is_blocked` and `SAFE_FILL_FIELDS` exactly
    as a dictionary match would — this function cannot reach those decisions,
    which is why nothing learned can cause a signature, a price or a sworn
    declaration to be filled.
    """
    if match is not None and getattr(match, "is_confident", False):
        return getattr(match, "canonical", None), "dictionary"

    lesson = lookup(company_id, label)
    if lesson is None:
        return (getattr(match, "canonical", None) if match else None), "dictionary"

    if lesson["kind"] == KIND_NOT_A_FIELD:
        return None, lesson["source"]

    return lesson["canonical_field"], lesson["source"]
