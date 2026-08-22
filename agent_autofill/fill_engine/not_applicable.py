"""
Fields the user has said do not apply, so the form can say "N/A" instead of
being left blank.

`Comprehensive_Tender_Document_Training_Guide.pdf` states this as the first of
four golden rules — "If your secretaries internalize these four rules, they will
avoid 90% of disqualification causes":

    1. Complete every field — use "N/A" if not applicable

It is not advice about tidiness. SBD 1's VAT row lists "Leaving blank instead of
'N/A'" as a named common mistake; SBD 4 says "If 'not applicable,' write 'N/A'
or 'None' — do NOT leave blank"; and the cross-cutting table gives the same rule
for all three procurement systems, South African, UN and World Bank.

AN EMPTY PROFILE IS NOT A DECLARATION

This is the whole reason the module exists rather than a one-line change to
`decide`. These are different facts:

    "we are not VAT registered"           -> N/A is the correct answer
    "nobody has told CairoAI our VAT no."  -> N/A is a false statement

The profile cannot tell them apart; both are an empty column. So nothing is
inferred from absence. A field lands here only when a person answered a direct
question about it, and the answer is stored with who said so and when, so a
wrong one can be found and undone.

WHAT MAY NEVER BE DECLARED

A signature, a price and a sworn declaration are refused by
`never_fill_fields.is_blocked` before `decide` reaches the N/A branch, so they
cannot be answered this way whatever is recorded here. Beyond that structural
guarantee, `DECLARABLE` is a short allow-list of fields where "not applicable"
is a truthful answer a company can actually give — you can genuinely have no
fax number and no VAT registration. You cannot genuinely have no company name.

The distinction the briefs draw still holds: "never write 'not applicable' where
a yes/no is required — it reads as avoiding disclosure." Nothing on this list is
a yes/no.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from agent import db
from agent.db_paths import AGENT_MEMORY_DB as DB_PATH

#: Canonical fields where "not applicable" is a truthful answer.
#:
#: Deliberately short, and every entry is a thing a real South African company
#: can lawfully not have. Absent from here: company_name, registration_number,
#: physical_address, contact_person — a bidder has all of those, and letting
#: "N/A" onto one would turn a missing detail into a written claim.
DECLARABLE: frozenset[str] = frozenset({
    # Only compulsory above the R1m turnover threshold, so a small supplier
    # genuinely has none — and SBD 1 asks for it by name and expects "N/A".
    "vat_registration_number",
    # Plenty of companies have no fax line at all any more.
    "fax_number",
    # A supplier not yet on the Central Supplier Database.
    "csd_number",
    # A company with no landline, trading from mobiles.
    "telephone_number",
})

# `director_names_and_id_numbers` WAS on this list and was removed, by a test.
#
# A sole proprietor genuinely has no directors, so it looked like the same kind
# of honest "N/A" as a missing fax number. It is not. The label
# "Name of State institution" maps to it, and that cell lives in SBD 4's
# declaration-of-interest table — the sworn one, where a wrong answer is not a
# typo but a false declaration to an organ of state.
#
# `is_blocked` does catch that label, but only when it is given the document
# context, and `pdf_filler` calls `decide` without it. So the declaration would
# have written "N/A" down a sworn table, which reads as CairoAI swearing on the
# bidder's behalf that no director is employed by the state.
#
# The guide does say SBD 4 must not be left blank. That is a task for the
# person signing it, not something CairoAI may answer from a stored preference.
# Restoring this entry requires context to reach `decide` first.

_schema_ready: set = set()


def _ensure_schema(conn) -> None:
    pid = os.getpid()
    if pid in _schema_ready:
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS autofill_not_applicable (
            company_id      TEXT NOT NULL,
            canonical_field TEXT NOT NULL,
            declared_by     TEXT,
            declared_at     TEXT NOT NULL,
            PRIMARY KEY (company_id, canonical_field)
        )
    """)
    conn.commit()
    _schema_ready.add(pid)


class NotDeclarable(ValueError):
    """Raised for a field where "N/A" would not be a truthful answer."""


def declare(company_id: str, canonical_field: str, declared_by: str = "user") -> dict:
    """
    Record that a field does not apply to this company.

    Refuses anything outside `DECLARABLE`. A caller trying to declare
    "company_name" not applicable has misunderstood the question it asked, and
    the refusal says so rather than writing "N/A" onto a government bid where
    the bidder's name belongs.
    """
    field = (canonical_field or "").strip()
    if not company_id:
        raise ValueError("company_id is required")
    if field not in DECLARABLE:
        raise NotDeclarable(
            f"'{field}' cannot be marked not applicable. Every bidder has one, "
            f"so 'N/A' there would be a false statement rather than an honest "
            f"blank. Fields that can: {', '.join(sorted(DECLARABLE))}."
        )

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        conn.execute(
            "INSERT OR IGNORE INTO autofill_not_applicable"
            " (company_id, canonical_field, declared_by, declared_at)"
            " VALUES (?, ?, ?, ?)",
            (company_id, field, declared_by or "user", now))
        conn.commit()

    return {"status": "success", "field": field, "value": "N/A"}


def withdraw(company_id: str, canonical_field: str) -> bool:
    """Undo a declaration. True if one went."""
    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        existed = conn.execute(
            "SELECT 1 FROM autofill_not_applicable"
            " WHERE company_id = ? AND canonical_field = ?",
            (company_id, canonical_field)).fetchone()
        conn.execute(
            "DELETE FROM autofill_not_applicable"
            " WHERE company_id = ? AND canonical_field = ?",
            (company_id, canonical_field))
        conn.commit()
    return bool(existed)


def declared_for(company_id: str) -> set:
    """The canonical fields this company has declared not applicable."""
    if not company_id:
        return set()
    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT canonical_field FROM autofill_not_applicable WHERE company_id = ?",
            (company_id,)).fetchall()
    # Intersected with DECLARABLE on the way out as well as on the way in, so
    # shrinking the list retires old declarations rather than leaving them live.
    return {r["canonical_field"] for r in rows} & DECLARABLE


def listed(company_id: str) -> list:
    """Every declaration with who made it and when, so it can be reviewed."""
    with db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM autofill_not_applicable WHERE company_id = ?"
            " ORDER BY declared_at DESC", (company_id,)).fetchall()
    return [{"field": r["canonical_field"], "declared_by": r["declared_by"],
             "declared_at": r["declared_at"], "value": "N/A"} for r in rows]
