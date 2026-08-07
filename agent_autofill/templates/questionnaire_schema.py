"""Definition of the company questionnaire that feeds Agent Autofill.

This module is pure data + pure validation. It performs no I/O and touches no
database, so it can be imported by the API layer, the tests and the fill engine
without pulling SQLite in behind it. Persistence lives in
``company_template_store``, which delegates to ``agent.memory.company_store``.

Why validation is strict here
-----------------------------
Answers collected once are replayed into every future bid. A transposed digit in
a VAT number is not caught later by anything -- the fill engine will faithfully
copy it onto SBD 1 for the next two years. So the questionnaire is the place to
reject malformed input, and it rejects loudly rather than "cleaning" values into
something plausible.

Where the rules are genuinely ambiguous (a foreign director with a passport
rather than an SA ID) the field is accepted with a warning rather than rejected,
because a false rejection blocks a legitimate bidder and that is its own harm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Fields Agent Autofill must NEVER fill from stored answers
# ---------------------------------------------------------------------------
# `signature` and `signature_date` are canonical fields in
# agent_autofill.extraction.field_alias_dictionary because they must be *found*
# on a form -- the draft has to leave the box visible and empty so a human can
# see what remains to be done. Finding them is not permission to fill them.
#
# A signature may only ever be applied by the signatory, personally, to the
# final document. See the SIGNATURE BOUNDARY comment in
# agent/memory/company_store.py.
#
# bid_number / bid_amount are excluded for a different reason: they are
# per-tender facts, not company facts, so they have no business in a stored
# company profile at all.
NEVER_AUTOFILL_FROM_PROFILE: frozenset = frozenset(
    {
        "signature",
        "signature_date",
        "bid_number",
        "bid_amount",
    }
)


# ---------------------------------------------------------------------------
# Validation primitives
# ---------------------------------------------------------------------------

# CIPC entity number: 2015/123456/07. Some older CC / external-company numbers
# carry a short alpha prefix (CK1998/012345/23), so the prefix is optional.
_REGISTRATION_RE = re.compile(r"^[A-Z]{0,3}\d{4}/\d{6}/\d{2}$", re.IGNORECASE)

# CSD supplier number: MAAA followed by digits.
_CSD_RE = re.compile(r"^MAAA\d{6,10}$", re.IGNORECASE)

# SARS income tax reference: 10 digits.
_TAX_REF_RE = re.compile(r"^\d{10}$")

# SARS VAT vendor number: 10 digits, always beginning with 4.
_VAT_RE = re.compile(r"^4\d{9}$")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

# SA numbers, tolerant of the ways humans type them: +27 11 555 1234,
# 011-555-1234, (011) 555 1234.
_PHONE_CLEAN_RE = re.compile(r"[\s\-\(\)\.]")
_PHONE_RE = re.compile(r"^(?:\+27\d{9}|0\d{9})$")

_SA_ID_RE = re.compile(r"^\d{13}$")

_BBBEE_CHOICES = [
    "Level 1", "Level 2", "Level 3", "Level 4",
    "Level 5", "Level 6", "Level 7", "Level 8",
    "Non-compliant",
]

_PROVINCE_CHOICES = [
    "Eastern Cape", "Free State", "Gauteng", "KwaZulu-Natal", "Limpopo",
    "Mpumalanga", "North West", "Northern Cape", "Western Cape",
]


class ValidationWarning(str):
    """A non-blocking note attached to an accepted answer."""


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def validate_sa_id_number(value: str) -> tuple[bool, str | None, str | None]:
    """
    Validate a director's identity number.

    Returns (ok, error, warning).

    A 13-digit value is treated as an SA ID and its Luhn check digit is
    verified, because the realistic failure here is a typo during data entry and
    the check digit exists precisely to catch that. Anything else is accepted
    with a warning: foreign directors legitimately hold passport numbers, and
    refusing them would block real bidders.
    """
    raw = (value or "").strip().replace(" ", "")
    if not raw:
        return False, "Identity number is required for every director.", None

    if _SA_ID_RE.match(raw):
        # YYMMDD prefix must be a plausible date.
        mm, dd = int(raw[2:4]), int(raw[4:6])
        if not (1 <= mm <= 12) or not (1 <= dd <= 31):
            return False, f"'{raw}' is not a valid SA ID number (date of birth section is impossible).", None
        if not _luhn_ok(raw):
            return False, f"'{raw}' fails the SA ID checksum -- check for a transposed digit.", None
        return True, None, None

    if raw.isdigit():
        return False, (
            f"'{raw}' is {len(raw)} digits. An SA ID number is exactly 13 digits. "
            "If this is a passport number, include the letters as they appear on the passport."
        ), None

    return True, None, ValidationWarning(
        f"'{raw}' is not an SA ID number, so it has been stored as supplied "
        "(passport or other identity document). It cannot be checksum-verified."
    )


def _v_registration(value):
    v = (value or "").strip()
    if not _REGISTRATION_RE.match(v):
        return f"'{v}' is not a CIPC registration number. Expected the form 2015/123456/07."
    return None


def _v_csd(value):
    v = (value or "").strip()
    if not _CSD_RE.match(v):
        return f"'{v}' is not a CSD supplier number. Expected the form MAAA0123456."
    return None


def _v_tax_ref(value):
    v = re.sub(r"\s", "", value or "")
    if not _TAX_REF_RE.match(v):
        return f"'{value}' is not a SARS tax reference number. Expected exactly 10 digits."
    return None


def _v_vat(value):
    v = re.sub(r"\s", "", value or "")
    if not _VAT_RE.match(v):
        return (
            f"'{value}' is not a SARS VAT number. A VAT vendor number is 10 digits "
            "and always starts with 4. Leave this blank if the company is not VAT registered."
        )
    return None


def _v_email(value):
    v = (value or "").strip()
    if not _EMAIL_RE.match(v):
        return f"'{v}' is not a valid email address."
    return None


def _v_phone(value):
    v = _PHONE_CLEAN_RE.sub("", value or "")
    if not _PHONE_RE.match(v):
        return (
            f"'{value}' is not a South African phone number. "
            "Expected 0XXXXXXXXX or +27XXXXXXXXX."
        )
    return None


def _v_bbbee(value):
    v = (value or "").strip()
    if not v:
        return None
    low = v.lower()
    # Accept both the select values and the descriptive forms that already exist
    # in the database ("Level 1 Contributor"), and bare integers 1-8. Rejecting a
    # value the database already holds would make the questionnaire unable to
    # display an existing profile.
    if low in {c.lower() for c in _BBBEE_CHOICES}:
        return None
    if re.match(r"^level\s*[1-8]\b", low):
        return None
    if re.match(r"^[1-8]$", low):
        return None
    if "non" in low and "compl" in low:
        return None
    return f"'{v}' is not a recognised B-BBEE status level."


def _v_nonempty_name(value):
    v = (value or "").strip()
    if len(v) < 2:
        return "Please supply a full name."
    return None


def _v_address(value):
    v = (value or "").strip()
    if len(v) < 8:
        return "Please supply a complete address including the postal code."
    return None


# ---------------------------------------------------------------------------
# Field specification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldSpec:
    key: str                       # questionnaire answer key
    label: str                     # what the user sees
    type: str                      # text | textarea | email | tel | select | director_list
    profile_column: str            # column in company_profile it persists to
    step: str                      # wizard step id
    required: bool = False
    help_text: str = ""
    placeholder: str = ""
    options: tuple = ()
    validator: Callable[[Any], str | None] | None = None
    # Canonical field name(s) in agent_autofill.extraction.field_alias_dictionary
    # that this answer is allowed to fill on a real form.
    fills_canonical: tuple = ()
    legal_note: str = ""


QUESTIONNAIRE_FIELDS: tuple = (
    # --- Step 1: the entity ------------------------------------------------
    FieldSpec(
        key="company_name",
        label="Registered company name",
        type="text",
        profile_column="company_name",
        step="entity",
        required=True,
        help_text="Exactly as it appears on the CIPC registration certificate, not the trading name.",
        placeholder="CairoAI (Pty) Ltd",
        validator=_v_nonempty_name,
        fills_canonical=("company_name",),
    ),
    FieldSpec(
        key="registration_number",
        label="Company registration number",
        type="text",
        profile_column="registration_number",
        step="entity",
        required=True,
        help_text="CIPC entity number.",
        placeholder="2026/250499/07",
        validator=_v_registration,
        fills_canonical=("registration_number",),
    ),
    FieldSpec(
        key="csd_number",
        label="CSD supplier number",
        type="text",
        profile_column="csd_number",
        step="entity",
        required=False,
        help_text=(
            "Central Supplier Database number. Registration on the CSD is a "
            "precondition for doing business with an organ of state, so a bid "
            "without it is normally rejected."
        ),
        placeholder="MAAA0123456",
        validator=_v_csd,
        fills_canonical=("csd_number",),
    ),
    FieldSpec(
        key="bbbee_level",
        label="B-BBEE status level",
        type="select",
        profile_column="bbbee_level",
        step="entity",
        required=False,
        options=tuple(_BBBEE_CHOICES),
        help_text="From your current, valid B-BBEE certificate or sworn affidavit.",
        validator=_v_bbbee,
        fills_canonical=("bbbee_level",),
    ),
    FieldSpec(
        key="province",
        label="Province",
        type="select",
        profile_column="province",
        step="entity",
        required=False,
        options=tuple(_PROVINCE_CHOICES),
    ),
    FieldSpec(
        key="registered_municipality",
        label="Registered municipality",
        type="text",
        profile_column="registered_municipality",
        step="entity",
        required=False,
        help_text="Locality preference points on some tenders depend on this.",
        placeholder="City of Tshwane",
    ),
    FieldSpec(
        key="industry",
        label="Primary industry",
        type="text",
        profile_column="industry",
        step="entity",
        required=False,
        placeholder="ICT & Professional Services",
    ),

    # --- Step 2: tax --------------------------------------------------------
    FieldSpec(
        key="tax_reference_number",
        label="SARS income tax reference number",
        type="text",
        profile_column="tax_reference_number",
        step="tax",
        required=True,
        help_text="10 digits, from your SARS correspondence or tax clearance.",
        placeholder="9012345678",
        validator=_v_tax_ref,
        fills_canonical=("tax_reference_number",),
    ),
    FieldSpec(
        key="vat_registration_number",
        label="VAT registration number",
        type="text",
        profile_column="vat_registration_number",
        step="tax",
        required=False,
        help_text=(
            "10 digits beginning with 4. Leave blank if the company is not a "
            "registered VAT vendor -- entering a number you do not hold is a "
            "misrepresentation on the bid."
        ),
        placeholder="4012345678",
        validator=_v_vat,
        fills_canonical=("vat_registration_number",),
    ),

    # --- Step 3: addresses --------------------------------------------------
    FieldSpec(
        key="physical_address",
        label="Physical address",
        type="textarea",
        profile_column="physical_address",
        step="addresses",
        required=True,
        help_text="Street address of the business, including postal code.",
        placeholder="Block C, 1 Sovereign Drive, Route 21 Corporate Park, Centurion, 0157",
        validator=_v_address,
        fills_canonical=("physical_address",),
    ),
    FieldSpec(
        key="postal_address",
        label="Postal address",
        type="textarea",
        profile_column="postal_address",
        step="addresses",
        required=False,
        help_text="Leave blank to reuse the physical address.",
        placeholder="PO Box 1234, Centurion, 0046",
        validator=_v_address,
        fills_canonical=("postal_address",),
    ),

    # --- Step 4: contact ----------------------------------------------------
    FieldSpec(
        key="standard_contact_person",
        label="Standard contact person",
        type="text",
        profile_column="standard_contact_person",
        step="contact",
        required=True,
        help_text="The person a procurement officer should call about a bid.",
        placeholder="Thabang Molwantwa",
        validator=_v_nonempty_name,
        fills_canonical=("contact_person",),
    ),
    FieldSpec(
        key="standard_phone",
        label="Standard contact number",
        type="tel",
        profile_column="standard_phone",
        step="contact",
        required=True,
        placeholder="+27 12 345 6789",
        validator=_v_phone,
        # One stored number fills whichever of the three boxes a given form has.
        fills_canonical=("telephone_number", "cell_phone_number"),
    ),
    FieldSpec(
        key="standard_email",
        label="Standard contact email",
        type="email",
        profile_column="standard_email",
        step="contact",
        required=True,
        placeholder="bids@example.co.za",
        validator=_v_email,
        fills_canonical=("email_address",),
    ),

    # --- Step 5: directors --------------------------------------------------
    FieldSpec(
        key="directors",
        label="Directors / members",
        type="director_list",
        profile_column="directors",
        step="directors",
        required=True,
        help_text=(
            "Every director or member of the entity, as listed at CIPC. "
            "SBD 4 requires each one to be declared."
        ),
        legal_note=(
            "Whether a director is in the service of the state is a sworn "
            "declaration on SBD 4. It is answered by you, never inferred by "
            "CairoAI, and a false declaration is grounds for the bid to be "
            "rejected and the bidder restricted."
        ),
    ),

    # --- Step 6: signatory --------------------------------------------------
    FieldSpec(
        key="authorized_signatory_name",
        label="Authorised signatory (name only)",
        type="text",
        profile_column="authorized_signatory_name",
        step="signatory",
        required=True,
        help_text=(
            "The name of the person authorised to sign bids for the company. "
            "This is used to print the name under the signature line."
        ),
        placeholder="Thabang Molwantwa",
        validator=_v_nonempty_name,
        # NOTE the absence of "signature" here, and its presence in
        # NEVER_AUTOFILL_FROM_PROFILE. This answer fills the printed NAME of the
        # signatory. It never fills the signature box.
        fills_canonical=(),
        legal_note=(
            "CairoAI never signs on your behalf. Drafts leave the signature and "
            "date blank for the signatory to complete by hand on the final "
            "document. No signature image is stored."
        ),
    ),
)


STEPS: tuple = (
    {
        "id": "entity",
        "title": "The entity",
        "blurb": "Who the bidder legally is. These come off your CIPC and CSD records.",
    },
    {
        "id": "tax",
        "title": "Tax",
        "blurb": "SARS identifiers that appear on almost every SBD 1.",
    },
    {
        "id": "addresses",
        "title": "Addresses",
        "blurb": "Where the business physically is, and where post reaches it.",
    },
    {
        "id": "contact",
        "title": "Contact",
        "blurb": "The person and details a procurement officer should use.",
    },
    {
        "id": "directors",
        "title": "Directors",
        "blurb": "Required for the SBD 4 declaration of interest.",
    },
    {
        "id": "signatory",
        "title": "Signatory",
        "blurb": "Who signs. CairoAI prints the name; the human signs the document.",
    },
)


FIELDS_BY_KEY = {f.key: f for f in QUESTIONNAIRE_FIELDS}
REQUIRED_FIELDS = tuple(f.key for f in QUESTIONNAIRE_FIELDS if f.required)
PROFILE_COLUMNS = tuple(f.profile_column for f in QUESTIONNAIRE_FIELDS)

# Bridge between the extraction subagent's canonical names and our storage
# columns. Kept explicit rather than derived so a rename on either side is a
# visible conflict rather than a silently empty draft field.
CANONICAL_TO_PROFILE_COLUMN = {
    canonical: f.profile_column
    for f in QUESTIONNAIRE_FIELDS
    for canonical in f.fills_canonical
}


# ---------------------------------------------------------------------------
# Director validation
# ---------------------------------------------------------------------------

def validate_directors(value) -> tuple[list, dict, list]:
    """
    Validate the directors array.

    Returns (cleaned, errors, warnings).

    `is_state_employee` must be an explicit boolean on every director. A missing
    value is an error, never a default of False: this drives the SBD 4
    declaration of interest, and quietly defaulting it to "no" would have
    CairoAI make a sworn declaration the user never made.
    """
    errors: dict = {}
    warnings: list = []

    if value in (None, "", []):
        return [], {"directors": "At least one director or member must be listed."}, []

    if not isinstance(value, list):
        return [], {"directors": "Directors must be supplied as a list."}, []

    cleaned = []
    for idx, entry in enumerate(value):
        prefix = f"directors[{idx}]"
        if not isinstance(entry, dict):
            errors[prefix] = "Each director must be an object with name, id_number and is_state_employee."
            continue

        name = str(entry.get("name") or "").strip()
        if len(name) < 2:
            errors[f"{prefix}.name"] = "Director name is required."

        ok, err, warn = validate_sa_id_number(entry.get("id_number"))
        if not ok:
            errors[f"{prefix}.id_number"] = err
        if warn:
            warnings.append(f"{prefix}.id_number: {warn}")

        raw_state = entry.get("is_state_employee", None)
        if isinstance(raw_state, bool):
            is_state = raw_state
        elif isinstance(raw_state, str) and raw_state.strip().lower() in ("true", "yes", "1"):
            is_state = True
        elif isinstance(raw_state, str) and raw_state.strip().lower() in ("false", "no", "0"):
            is_state = False
        else:
            errors[f"{prefix}.is_state_employee"] = (
                "Answer required: is this director in the service of the state? "
                "This is a sworn declaration on SBD 4 and cannot be left blank or assumed."
            )
            is_state = None

        cleaned.append({
            "name": name,
            "id_number": str(entry.get("id_number") or "").strip(),
            "is_state_employee": is_state,
        })

    return cleaned, errors, warnings


# ---------------------------------------------------------------------------
# Whole-questionnaire validation
# ---------------------------------------------------------------------------

def validate_answers(answers: dict, partial: bool = False) -> dict:
    """
    Validate a full or partial set of questionnaire answers.

    `partial=True` skips the required-field check, so a half-finished wizard
    step can still be checked for format errors without being told off for
    fields the user has not reached yet.

    Returns {"valid", "errors", "warnings", "missing_required", "cleaned"}.
    """
    answers = answers or {}
    errors: dict = {}
    warnings: list = []
    cleaned: dict = {}
    missing_required: list = []

    unknown = [k for k in answers if k not in FIELDS_BY_KEY]
    for k in unknown:
        errors[k] = f"'{k}' is not a questionnaire field."

    for spec in QUESTIONNAIRE_FIELDS:
        raw = answers.get(spec.key, None)

        if spec.type == "director_list":
            if spec.key not in answers:
                if spec.required and not partial:
                    missing_required.append(spec.key)
                continue
            cleaned_dirs, dir_errors, dir_warnings = validate_directors(raw)
            errors.update(dir_errors)
            warnings.extend(dir_warnings)
            cleaned[spec.key] = cleaned_dirs
            if spec.required and not cleaned_dirs and not partial:
                missing_required.append(spec.key)
            continue

        is_blank = raw is None or (isinstance(raw, str) and not raw.strip())

        if is_blank:
            if spec.required and not partial:
                missing_required.append(spec.key)
            if spec.key in answers:
                cleaned[spec.key] = None
            continue

        value = raw.strip() if isinstance(raw, str) else raw

        if spec.validator is not None:
            err = spec.validator(value)
            if err:
                errors[spec.key] = err
                continue

        cleaned[spec.key] = value

    if missing_required:
        for key in missing_required:
            errors.setdefault(key, f"{FIELDS_BY_KEY[key].label} is required.")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "missing_required": missing_required,
        "cleaned": cleaned,
    }


def answers_to_profile_fields(cleaned: dict) -> dict:
    """Rekey validated answers onto company_profile column names."""
    out = {}
    for key, value in (cleaned or {}).items():
        spec = FIELDS_BY_KEY.get(key)
        if spec is None:
            continue
        out[spec.profile_column] = value
    return out


def profile_to_answers(profile: dict) -> dict:
    """Rekey a stored company_profile row back onto questionnaire answer keys."""
    out = {}
    for spec in QUESTIONNAIRE_FIELDS:
        if spec.profile_column in (profile or {}):
            out[spec.key] = profile[spec.profile_column]
    return out


def describe() -> dict:
    """JSON-serialisable description of the questionnaire, for the UI."""
    return {
        "steps": [
            {
                **step,
                "fields": [
                    {
                        "key": f.key,
                        "label": f.label,
                        "type": f.type,
                        "required": f.required,
                        "help_text": f.help_text,
                        "placeholder": f.placeholder,
                        "options": list(f.options),
                        "legal_note": f.legal_note,
                    }
                    for f in QUESTIONNAIRE_FIELDS
                    if f.step == step["id"]
                ],
            }
            for step in STEPS
        ],
        "required_fields": list(REQUIRED_FIELDS),
        "never_autofilled": sorted(NEVER_AUTOFILL_FROM_PROFILE),
    }
