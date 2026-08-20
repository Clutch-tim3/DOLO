"""
What a pack needs and the profile does not have.

P0-2. The owner: "anything personal like name or ID number should be asked by
agent, agent should ask for clarification wherever it isn't sure of things."

A field with no data became a line in a flag list the user had to find, read
and acknowledge one at a time. For a director's ID number — a value the user
has and the system simply does not — that is the wrong interaction entirely.
The system knows precisely what it needs; it should ask.

WHY THIS DEDUPLICATES

On the owner's pack, 24 outstanding fields map to 12 distinct profile columns:
"Designation" appears 7 times and "Capacity" 6, and both are
authorized_signatory_capacity. Asking thirteen times for one fact is the same
failure as flagging it thirteen times.

WHAT IT DOES NOT DO

Decide anything, or write anything. It reports what is missing and what to ask.
The write goes through `update_company_profile` with `confirmed=True` meaning
what it says — the user saw that specific value and approved it.
"""

from __future__ import annotations

from agent_autofill.fill_engine.safe_fill_fields import SAFE_FILL_FIELDS

#: What to ask for each profile column, in the words a person would use.
#: Absent from here means the field is fillable but has no sensible question,
#: and it is reported without a prompt rather than invented.
FIELD_PROMPTS: dict[str, str] = {
    "company_name": "the registered name of your company",
    "registration_number": "your CIPC company registration number",
    "csd_number": "your CSD supplier number (the MAAA number)",
    "bbbee_level": "your B-BBEE contributor level (1 to 8)",
    "tax_reference_number": "your SARS tax reference number",
    "vat_registration_number": "your VAT registration number, if you are registered",
    "tax_compliance_pin": "your SARS Tax Compliance Status PIN",
    "physical_address": "your physical (street) address",
    "postal_address": "your postal address",
    "standard_contact_person": "the name of your standard contact person",
    "standard_phone": "your landline telephone number",
    "standard_cell": "your cellphone number",
    "standard_fax": "your fax number, if you have one",
    "standard_email": "your email address",
    "authorized_signatory_capacity":
        "the capacity of the person who signs bids — for example Director or "
        "Managing Member",
    "directors": "the full name and ID number of each director",
}

#: Fields carrying personal information about an identifiable person. Worth
#: naming so the agent asks for them with the care they deserve rather than as
#: another blank to fill.
PERSONAL_FIELDS = frozenset({
    "directors", "standard_contact_person", "standard_cell", "standard_phone",
    "standard_email",
})

#: A sworn declaration on SBD 4, not a fact to look up. It lives inside
#: `directors` and must be ASKED — never inferred, never defaulted, never
#: carried over from another tender. The tools schema says so and it is right.
SWORN_SUBFIELDS = {
    "is_state_employee": (
        "whether that person is in the service of the state. This is a sworn "
        "declaration on SBD 4 — it must be answered by the person, and CairoAI "
        "never assumes it."
    ),
}


def missing_profile_fields(outstanding_rows, profile: dict) -> list[dict]:
    """
    The distinct profile fields a pack is waiting on, and what to ask for each.

    `outstanding_rows` are the review's unacknowledged items; `profile` is the
    company profile as it stands. Returns one entry per COLUMN, not per blank,
    with the labels that asked for it so the agent can say where it is needed.

    A field already carrying a value is never reported, which is what makes
    "asked for once, never asked again" true: once written, it fills.
    """
    from agent_autofill.fill_engine.safe_fill_fields import is_sentinel

    wanted: dict[str, dict] = {}

    for row in outstanding_rows or []:
        reason = (row.get("reason") or "")
        # Only "nothing on file" — a blocked signature or a pricing refusal is
        # not something the user can answer, and asking would be noise.
        if not reason.startswith("Nothing on file"):
            continue

        canonical = row.get("canonical_field") or _canonical_from_label(row.get("label"))
        if not canonical:
            continue
        column = SAFE_FILL_FIELDS.get(canonical)
        if not column:
            continue

        # Already answered. Sentinels count as absent, as everywhere else.
        current = (profile or {}).get(column)
        if current is not None and not is_sentinel(current):
            continue

        entry = wanted.setdefault(column, {
            "field": column,
            "prompt": FIELD_PROMPTS.get(column),
            "personal": column in PERSONAL_FIELDS,
            "asked_by": [],
            "locations": set(),
            "count": 0,
        })
        entry["count"] += 1
        entry["locations"].add(row.get("location") or "")
        label = (row.get("label") or "").strip()
        if label and label not in entry["asked_by"] and len(entry["asked_by"]) < 5:
            entry["asked_by"].append(label)

    out = []
    for entry in wanted.values():
        entry["locations"] = sorted(l for l in entry["locations"] if l)
        if entry["field"] == "directors":
            # The one place a follow-up question is mandatory.
            entry["also_ask"] = dict(SWORN_SUBFIELDS)
        out.append(entry)

    # Most-needed first: the field blocking seven blanks is worth asking about
    # before the one blocking a single blank.
    out.sort(key=lambda e: (-e["count"], e["field"]))
    return out


def _canonical_from_label(label: str | None) -> str | None:
    """Map a form label back to a canonical field, or None."""
    if not label:
        return None
    try:
        from agent_autofill.extraction import match_label

        match = match_label(label)
    except Exception:  # noqa: BLE001 - asking is best-effort, never fatal
        return None

    if match is None:
        return None
    # AliasMatch calls it `canonical`.
    return getattr(match, "canonical", None)
