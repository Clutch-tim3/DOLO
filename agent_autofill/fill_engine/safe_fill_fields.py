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


def resolve_value(canonical_field: str, profile: dict) -> tuple[str | None, str | None]:
    """
    Return (value, source) for a canonical field, or (None, None).

    The confirmed company profile is the ONLY source. Values parsed out of the
    tender document are never written back into it — a tender is a counterparty
    document and must not be able to change what we believe about our own
    company.
    """
    column = SAFE_FILL_FIELDS.get(canonical_field)
    if not column:
        return None, None

    raw = (profile or {}).get(column)

    if column == "directors":
        return _format_directors(raw), "company profile (directors)"
    if canonical_field == "bbbee_level":
        return _format_bbbee(raw), "company profile"

    if raw in (None, ""):
        return None, None
    return str(raw).strip(), "company profile"


def decide(canonical_field: str | None, label_text: str | None,
           profile: dict, match_score: float = 100.0,
           context: set[str] | None = None) -> FillDecision:
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
    block = is_blocked(label_text, canonical_field, context)
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
