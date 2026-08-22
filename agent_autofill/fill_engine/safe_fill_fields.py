"""
The whitelist.

Only the fields named here are ever written into a document automatically, and
only after never_fill_fields.is_blocked() has declined to block them. Anything
not on this list is left for the human, even if the alias dictionary matched it
confidently.

The list is deliberately limited to *verifiable company facts* — things that
appear on a registration certificate or a CSD report and cannot be a matter of
judgement. A wrong company registration number is a clerical error the reviewer
will spot. A wrong answer to "are you related to a state employee" is a false
declaration. Only the first kind belongs here.

Order of authority when both a stored profile value and a parsed document value
exist: the confirmed company profile wins. Parsed document values are evidence,
not truth — see resolve_value().
"""

from __future__ import annotations

from dataclasses import dataclass

from .never_fill_fields import BlockDecision, is_blocked

#: Canonical field -> the company_profile column it is drawn from.
#: If a canonical field is not a key here, it is NOT auto-fillable.
SAFE_FILL_FIELDS: dict[str, str] = {
    "company_name": "company_name",
    "registration_number": "registration_number",
    "csd_number": "csd_number",
    "bbbee_level": "bbbee_level",
    "tax_reference_number": "tax_reference_number",
    "vat_registration_number": "vat_registration_number",
    "physical_address": "physical_address",
    "postal_address": "postal_address",
    "contact_person": "standard_contact_person",
    # Landline and mobile draw from separate columns. Pointing both at one
    # phone column put the same number on MBD 1's TELEPHONE and CELLPHONE rows,
    # which is visibly wrong on a submitted form.
    "telephone_number": "standard_phone",
    "cell_phone_number": "standard_cell",
    "fax_number": "standard_fax",
    "email_address": "standard_email",
    "tax_compliance_pin": "tax_compliance_pin",
    # Who signs, not the signature. "Director", "Managing Member".
    "capacity": "authorized_signatory_capacity",
    "director_names_and_id_numbers": "directors",
    # SBD 6.1's points claim. NOT a stored fact — a derived one, computed by
    # `preference_points` from the B-BBEE level on the certificate and the
    # preference system the tender itself states, and put on the profile dict
    # by the orchestrator for this document only. It is never written to the
    # database, because it is true of one tender rather than of the company.
    #
    # It is absent unless BOTH inputs are known, so this fills or it does not:
    # a Level 1 bidder claims 20 points under 80/20 and 10 under 90/10, and
    # there is no halfway answer worth writing onto a bid.
    "bbbee_points_claim": "bbbee_points_claim",
}

#: Fields that are safe in principle but frequently ambiguous on SA forms, so
#: they are filled at reduced confidence and always listed in the review
#: summary for a closer look.
LOW_CONFIDENCE_FIELDS = {"bbbee_level", "capacity"}

#: Minimum alias-match score before a value is written. Below this the field is
#: reported as a low-confidence match and left blank rather than guessed.
MIN_FILL_SCORE = 88.0


@dataclass(frozen=True)
class FillDecision:
    fill: bool
    value: str | None = None
    source: str | None = None
    reason: str | None = None
    low_confidence: bool = False
    block: BlockDecision | None = None


def _format_directors(directors) -> str | None:
    """Directors render as a factual roster only — names and ID numbers."""
    if not isinstance(directors, list) or not directors:
        return None
    parts = []
    for d in directors:
        if not isinstance(d, dict):
            continue
        name = (d.get("name") or "").strip()
        idn = (d.get("id_number") or "").strip()
        if name and idn:
            parts.append(f"{name} ({idn})")
        elif name:
            parts.append(name)
    return "; ".join(parts) or None


def _format_bbbee(value) -> str | None:
    """
    bbbee_level is declared INTEGER but holds strings like
    'Level 1 Contributor' in existing rows. Render whatever is stored in a
    consistent way rather than pretending the column is clean.
    """
    if value in (None, ""):
        return None
    s = str(value).strip()
    if s.isdigit():
        return f"Level {s}"
    return s


#: Values that mean "we do not know this", stored as though they were answers.
#:
#: The Vault writes "Pending" into registration_number when it cannot extract
#: one, and that string reached a bid form as the company's registration
#: number. A placeholder is not a value; it is the absence of one wearing a
#: label, and on a document submitted to an organ of state it is worse than a
#: blank, because a blank is visibly unfinished and "Pending" reads as an
#: answer.
#:
#: Compared case-insensitively after trimming.
SENTINEL_VALUES = frozenset({
    "pending", "n/a", "na", "n.a.", "tbc", "tba", "unknown", "none",
    "not applicable", "not available", "nil", "-", "--", "0",
    "null", "undefined", "to be confirmed", "to be advised",
})

#: What goes on the form for a field the user has declared does not apply.
#: "N/A" and "None" are both accepted by the guide; "N/A" is what SBD 1's own
#: VAT row instructs.
NOT_APPLICABLE = "N/A"

#: Where the declared-not-applicable set travels on the profile dict. Underscore
#: prefixed because it is not a profile COLUMN — it is assembled per fill from
#: `not_applicable.declared_for`, the same way `bbbee_points_claim` is.
#:
#: Note this is NOT the sentinel list below. A sentinel is a junk placeholder
#: somebody typed into a field and means "absent". This is a person answering
#: "no, we are not VAT registered" and means "absent ON PURPOSE, and the form
#: should say so".
DECLARED_NA_KEY = "_declared_not_applicable"

#: bbbee_level 9 is this codebase's sentinel for "non-compliant or unknown" —
#: the recognised scale is 1-8, and get_bbbee_points returns 0.0 for anything
#: outside it. app.py writes 9 when the Vault extracts nothing, so 9 conflates
#: "we could not read it" with "they are non-compliant". Neither belongs on a
#: form as a level.
BBBEE_SENTINEL_LEVELS = frozenset({0, 9})


def is_sentinel(value) -> bool:
    """Whether a stored value is a placeholder rather than a fact."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in SENTINEL_VALUES or not value.strip()
    return False


def resolve_value(canonical_field: str, profile: dict) -> tuple[str | None, str | None]:
    """
    Return (value, source) for a canonical field, or (None, None).

    The confirmed company profile is the ONLY source. Values parsed out of the
    tender document are never written back into it — a tender is a counterparty
    document and must not be able to change what we believe about our own
    company.

    A sentinel is treated as absent. This is the last gate before a value is
    drawn onto a form, so it holds regardless of how the placeholder got into
    the profile — and the field then appears in the review list as something
    left for a person, which is the truthful description.
    """
    column = SAFE_FILL_FIELDS.get(canonical_field)
    if not column:
        return None, None

    raw = (profile or {}).get(column)

    if column == "directors":
        return _format_directors(raw), "company profile (directors)"

    if canonical_field == "bbbee_level":
        # 9 and 0 mean "not established", however they are spelled.
        try:
            if int(str(raw).strip()) in BBBEE_SENTINEL_LEVELS:
                return None, None
        except (TypeError, ValueError):
            pass
        if is_sentinel(raw):
            return None, None
        return _format_bbbee(raw), "company profile"

    if is_sentinel(raw):
        return None, None
    return str(raw).strip(), "company profile"


def decide(canonical_field: str | None, label_text: str | None,
           profile: dict, match_score: float = 100.0,
           context: set[str] | None = None,
           section: str | None = None) -> FillDecision:
    """
    The single decision point for whether a detected blank gets written.

    Blocklist is consulted FIRST and unconditionally. A field can be on the
    whitelist and still be refused — e.g. a label reading
    "Signature of the person whose name appears above" would map to a name-ish
    field but is a signature line.

    `context` is the document-level classification (see
    never_fill_fields.classify_document_context). Callers should always pass it:
    without it, an SBD 4 declaration table looks like a set of ordinary name and
    ID fields.
    """
    block = is_blocked(label_text, canonical_field, context, section)
    if block.blocked:
        return FillDecision(False, reason=block.message, block=block)

    if not canonical_field:
        return FillDecision(False, reason="No canonical field matched this label.")

    if canonical_field not in SAFE_FILL_FIELDS:
        return FillDecision(
            False,
            reason="Not on the auto-fill whitelist — left for you to complete.",
        )

    if match_score < MIN_FILL_SCORE:
        return FillDecision(
            False,
            reason=(f"Label matched '{canonical_field}' at only {match_score:.0f}%, "
                    f"below the {MIN_FILL_SCORE:.0f}% needed to fill automatically."),
            low_confidence=True,
        )

    value, source = resolve_value(canonical_field, profile)
    if value is None:
        # The user has said this field does not apply to their company, so the
        # correct answer is "N/A" rather than a blank line.
        #
        # `Comprehensive_Tender_Document_Training_Guide.pdf`, golden rule 1 of
        # four: "Complete every field — use 'N/A' if not applicable." It lists
        # "Leaving blank instead of 'N/A'" as a named common mistake on SBD 1's
        # VAT row, and SBD 4 is blunter: "If 'not applicable,' write 'N/A' or
        # 'None' — do NOT leave blank." All three procurement systems in that
        # guide — SBD, UNGM and World Bank — say the same thing.
        #
        # AN EMPTY PROFILE IS NOT A DECLARATION. "We are not VAT registered" and
        # "nobody has told CairoAI the VAT number" are different facts and only
        # the first one may be written on a bid; writing N/A for the second is a
        # false statement to an organ of state. So this reads a set the USER
        # populated by answering a direct question, never the absence of a value.
        #
        # It sits AFTER the blocklist and the whitelist deliberately. A
        # signature, a price and a sworn declaration cannot reach this line, so
        # no declaration can put "N/A" on one — and "never write 'not
        # applicable' where a yes/no is required, it reads as avoiding
        # disclosure" stays true.
        if canonical_field in (profile or {}).get(DECLARED_NA_KEY, ()):
            return FillDecision(
                True,
                value=NOT_APPLICABLE,
                source="declared not applicable by you",
            )
        return FillDecision(
            False,
            reason="Nothing on file for this field yet — add it in your company profile.",
        )

    return FillDecision(
        True,
        value=value,
        source=source,
        low_confidence=canonical_field in LOW_CONFIDENCE_FIELDS,
    )
