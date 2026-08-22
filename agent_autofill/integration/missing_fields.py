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
    # Derived, not stored — `preference_points` computes it from the B-BBEE
    # level and the system the tender states. So the question is not "what is
    # your claim", which the user should not have to work out, but the one fact
    # CairoAI could not read off the form. On his Johannesburg Water RFQ both
    # 80/20 and 90/10 appear as completed options, because the buyer left both
    # in; there is no answer to deduce and one to ask for.
    "bbbee_points_claim":
        "which preference point system this tender uses — 80/20 or 90/10. The "
        "form states both and does not say which applies, and the claim is "
        "double under one of them",
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
        category = (row.get("category") or "")

        # Two kinds of question, and only two. A blocked signature or a pricing
        # refusal is not something the user can answer, so asking would be noise.
        #
        #   "Nothing on file"  — we know the field and have no value for it.
        #   ambiguous_label    — we cannot tell WHICH field it is.
        #
        # The second was skipped here, and that was the bug. A bare "ADDRESS" is
        # refused because postal and physical are different answers and writing
        # the wrong one onto a bid is worse than leaving it — correct. But then
        # nothing followed up, so the owner got a blank line instead of a
        # three-second question. His words: "i keep telling you the agent needs
        # to ask me if its not certain, postal address or physical address ill
        # answer all that needs to be answered."
        #
        # Being unsure is the best possible reason to ask. It was the one case
        # that never did.
        if category == "ambiguous_label":
            choice = _ambiguous_choice(row.get("label"))
            if choice is None:
                continue
            column, prompt = choice
            entry = wanted.setdefault(column, {
                "field": column,
                "prompt": prompt,
                "personal": column in PERSONAL_FIELDS,
                "asked_by": [],
                "locations": set(),
                "count": 0,
                # The agent must not treat this as "supply a missing fact". The
                # profile may well hold both values already; what is missing is
                # which one this form wants.
                "kind": "which_one",
            })
            entry["count"] += 1
            entry["locations"].add(row.get("location") or "")
            label = (row.get("label") or "").strip()
            if label and label not in entry["asked_by"] and len(entry["asked_by"]) < 5:
                entry["asked_by"].append(label)
            continue

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
            # We know the field; we have no value for it. Contrast "which_one",
            # where we may well have the value and not know which is wanted.
            "kind": "supply",
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


#: A too-general label, and the choice to put to the user. The key is the
#: profile column the ANSWER is written to; the question names both options so
#: the user picks rather than guesses what we meant.
#:
#: Deliberately small. A label is only listed here when the ambiguity is
#: genuinely between two fields CairoAI already holds — never as a way of
#: turning a label we do not understand into a question we invented.
_AMBIGUOUS_CHOICES: dict[str, tuple[str, str]] = {
    "ADDRESS": ("physical_address",
                "which address this form wants — your physical (street) "
                "address or your postal address"),
    "NAME": ("company_name",
             "whether this asks for your company's registered name or for a "
             "person's name"),
    "NUMBER": ("registration_number",
               "which number this asks for — your CIPC registration number, "
               "your CSD number, or a telephone number"),
    "TEL": ("standard_phone",
            "whether this wants your landline or your cellphone number"),
    "TELEPHONE": ("standard_phone",
                  "whether this wants your landline or your cellphone number"),
    "CONTACT": ("standard_contact_person",
                "whether this asks for your contact person's name or for a "
                "contact number"),
}


def _ambiguous_choice(label: str | None) -> tuple[str, str] | None:
    """The choice to put to the user for a too-general label, or None."""
    text = (label or "").strip().upper().rstrip(":").strip()
    if not text:
        return None
    # Exact match only. "PHYSICAL ADDRESS" is not ambiguous and must not be
    # dragged in here by a substring test — it already fills.
    return _AMBIGUOUS_CHOICES.get(text)


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
