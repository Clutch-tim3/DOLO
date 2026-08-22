"""Map a label scraped off a tender form to a canonical Agent Autofill field.

Why this module is paranoid
---------------------------
The output feeds a fill engine. Writing a VAT number into the company
registration box does not produce a slightly-wrong draft — it produces a bid
that a procurement officer can disqualify. So every design choice here breaks
towards ``unmatched``:

* A fuzzy score alone is never sufficient. SA tender forms are full of labels
  that are lexically near-identical but semantically different — "Company
  Registration Number" vs "VAT Registration Number" share two of three tokens
  and score ~80 against each other on most ratios.
* ``AMBIGUITY_MARGIN`` therefore requires the winning canonical field to beat
  the runner-up canonical field by a clear margin. A label that fits two fields
  almost equally well is reported ambiguous, not assigned.
* ``UNSAFE_GENERIC_LABELS`` hard-blocks bare labels ("Registration Number",
  "Number", "Name") that are genuinely under-determined no matter how they
  score. On a real form these are disambiguated by a section heading that this
  module deliberately does not see.

Threshold is 85 (per spec) on ``rapidfuzz.fuzz.WRatio`` over normalized labels.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

from rapidfuzz import fuzz, process

# Score a normalized label must reach before any mapping is considered.
MATCH_THRESHOLD = 85.0

# The best canonical field must beat the second-best canonical field by at least
# this many points. Prevents "Registration Number" style coin-flips.
AMBIGUITY_MARGIN = 8.0

# Second opinion required before any fuzzy match is accepted.
#
# ``fuzz.WRatio`` switches to a *partial* (substring) comparison scaled by a
# length-difference penalty when the two strings differ much in length. That path
# emits a flat ~85.5 (95 x 0.9) for pairs sharing only an incidental substring,
# which is above the 85 threshold. Measured on real SBD prose from
# BID_DOCUMENT_06FY27, every one of these artifacts scored WRatio 85.5:
#
#   "I, the"                                   -> "NAME OF THE BIDDER"   (company_name)
#   "the following"                            -> "NAME OF THE ENTERPRISE"
#   "visited and examined the site on (date)"  -> "NAME OF THE BIDDER"
#   "certify to be true and complete ..."      -> "IN RESPECT OF BID NO" (bid_number)
#
# Any of those would have written a company name into a signature or date box.
# ``token_sort_ratio``/``ratio`` have no partial-match path, so requiring one of
# them to clear this floor removes the artifacts. Calibration on the same
# document: genuine variant spellings corroborate at 87-100, artifacts at <=67.
CORROBORATION_FLOOR = 75.0


# --------------------------------------------------------------------------
# Canonical fields
# --------------------------------------------------------------------------
# Ordered dict of canonical name -> human description. The fill engine consumes
# these keys; do not rename one without migrating the engine.
CANONICAL_FIELDS: dict[str, str] = {
    "company_name": "Registered trading name of the bidding entity",
    "registration_number": "CIPC company/CC registration number (e.g. 2015/123456/07)",
    "tax_reference_number": "SARS income tax reference number",
    "tax_compliance_pin": "SARS Tax Compliance Status (TCS) PIN",
    "vat_registration_number": "SARS VAT vendor registration number",
    "csd_number": "Central Supplier Database registration number (MAAA...)",
    "bbbee_level": "B-BBEE status level of contribution (1-8 / non-compliant)",
    "physical_address": "Street / physical address of the bidder",
    "postal_address": "Postal address of the bidder",
    "contact_person": "Name of the bidder's contact person / representative",
    "telephone_number": "Landline telephone number",
    "cell_phone_number": "Mobile number",
    "fax_number": "Facsimile number",
    "email_address": "Email address",
    "cidb_registration": "CIDB contractor grading designation / registration number",
    "bid_number": "The tender / bid reference number being responded to",
    "bid_amount": "Total tendered amount",
    "identity_number": "RSA identity number of the signatory / declared person (MBD 4)",
    "signature": "Signature of the authorised representative",
    "signature_date": "Date the form is signed",
    "capacity": "Capacity in which the signatory signs (e.g. Director)",
}


# --------------------------------------------------------------------------
# Alias seeds
# --------------------------------------------------------------------------
# Each entry is a real phrasing observed on SA SBD/MBD forms and tender packs.
# Variants marked (spec) are the ones handed down in the build brief.
ALIAS_SEEDS: dict[str, tuple[str, ...]] = {
    "company_name": (
        "BIDDER NAME",                                       # (spec)
        "Name of Bidder",                                    # (spec)
        "NAME OF BIDDER",                                    # (spec)
        "Full Name of bidder or his or her representative",  # (spec)
        "NAME OF THE BIDDER",
        "NAME OF FIRM",
        "NAME OF COMPANY",
        "NAME OF TENDERER",
        "TENDERER NAME",
        "NAME OF ENTITY",
        "NAME OF THE ENTERPRISE",
        "REGISTERED NAME OF BIDDER",
        "COMPANY NAME",
        "NAME OF SUPPLIER",
        "NAME OF THE COMPANY",
        "SUPPLIER NAME",
        "NAME OF BIDDING ENTITY",
        "NAME OF BIDDER ENTITY",
    ),
    "registration_number": (
        "Company Registration Number",                       # (spec)
        "COMPANY REGISTRATION NUMBER",                       # (spec)
        "COMPANY / CC REGISTRATION NUMBER",
        "COMPANY OR CC REGISTRATION NUMBER",
        "CIPC REGISTRATION NUMBER",
        "COMPANY REGISTRATION NO",
        "ENTERPRISE REGISTRATION NUMBER",
        "BUSINESS REGISTRATION NUMBER",
        "REGISTRATION NUMBER OF COMPANY",
        "COMPANY REGISTRATION NUMBER OF BIDDER",
    ),
    "tax_reference_number": (
        "Tax Reference Number",                              # (spec)
        "TAX REFERENCE NUMBER",
        "INCOME TAX REFERENCE NUMBER",
        "SARS TAX REFERENCE NUMBER",
        "TAX REFERENCE NO",
        "TAX NUMBER",
    ),
    "tax_compliance_pin": (
        "TAX COMPLIANCE STATUS PIN",
        "TAX COMPLIANCE SYSTEM PIN",
        "TCS PIN",
        "SUPPLIER TAX COMPLIANCE STATUS PIN",
        "TAX COMPLIANCE STATUS TCS PIN",
        # Forms word this loosely. "Valid Compliant Tax pin" is verbatim from a
        # real RFQ and scored 86 — near-miss, then correctly refused by
        # corroboration.
        "VALID COMPLIANT TAX PIN",
        "COMPLIANT TAX PIN",
        "TAX COMPLIANCE PIN",
        "TAX PIN",
        "VALID TAX COMPLIANCE PIN",
        "TCS PIN NUMBER",
    ),
    "vat_registration_number": (
        "VAT Registration Number",                           # (spec)
        "VAT REGISTRATION NUMBER",
        "VAT REGISTRATION NO",
        "VAT NUMBER",
        "VAT NO",
        "VAT VENDOR NUMBER",
        "VAT REGISTRATION NUMBER IF APPLICABLE",
    ),
    "csd_number": (
        "CSD Number",                                        # (spec)
        "CSD NUMBER",
        "CSD REGISTRATION NUMBER",
        "CENTRAL SUPPLIER DATABASE NUMBER",
        "CENTRAL SUPPLIER DATABASE No",
        "UNIQUE REGISTRATION REFERENCE NUMBER CSD",
        "SUPPLIER NUMBER CSD",
        "MAAA NUMBER",
    ),
    "bbbee_level": (
        "B-BBEE Status Level",                               # (spec)
        "BBBEE Status Level of Contribution",                # (spec)
        "B-BBEE STATUS LEVEL OF CONTRIBUTION",
        "BBBEE STATUS LEVEL",
        "B-BBEE LEVEL",
        "BBBEE LEVEL OF CONTRIBUTION",
        "BROAD BASED BLACK ECONOMIC EMPOWERMENT STATUS LEVEL",
        "B-BBEE STATUS LEVEL OF CONTRIBUTOR",
    ),
    # SBD 6.1's claim box. The wording varies between the PPR 2017 and PPR 2022
    # editions, and both are still issued.
    "bbbee_points_claim": (
        "B-BBEE STATUS LEVEL OF CONTRIBUTION CLAIMED",
        "NUMBER OF POINTS CLAIMED",
        "POINTS CLAIMED",
        "PREFERENCE POINTS CLAIMED",
        "B-BBEE POINTS CLAIMED",
        "NUMBER OF PREFERENCE POINTS CLAIMED",
        "POINTS CLAIMED FOR B-BBEE STATUS LEVEL OF CONTRIBUTION",
    ),
    "physical_address": (
        "PHYSICAL ADDRESS",
        "STREET ADDRESS",
        "BUSINESS ADDRESS",
        "PHYSICAL BUSINESS ADDRESS",
        "ADDRESS OF BIDDER",
        # Real forms say "supplier" at least as often as "bidder". These scored
        # 86 on WRatio against ADDRESS OF BIDDER and then failed corroboration,
        # because supplier and bidder are not the same word — the second
        # opinion was doing its job. The answer is the alias, not a lower floor.
        "ADDRESS OF SUPPLIER",
        "SUPPLIER ADDRESS",
        "COMPANY ADDRESS",
        "ADDRESS OF THE BIDDER",
        "ADDRESS OF THE SUPPLIER",
        # A bare "ADDRESS" is deliberately NOT here. It sits on the unsafe
        # list with NAME, AMOUNT, CODE and LEVEL — single words that could mean
        # anything on a form, and that check runs before this index, so adding
        # it here would be dead code that reads like a decision. A form asking
        # only "ADDRESS" has to be asked about, not guessed at: postal and
        # physical are different answers and only one of them is right.
    ),
    "postal_address": (
        "POSTAL ADDRESS",
        "POST ADDRESS",
        "POSTAL ADDRESS OF BIDDER",
        "POSTAL ADDRESS OF SUPPLIER",
        "POSTAL CODE ADDRESS",
    ),
    "contact_person": (
        "CONTACT PERSON",
        "NAME OF CONTACT PERSON",
        "CONTACT PERSON NAME",
        "AUTHORISED REPRESENTATIVE",
        "NAME OF AUTHORISED REPRESENTATIVE",
        "NAME OF REPRESENTATIVE",
    ),
    "telephone_number": (
        "TELEPHONE NUMBER",
        "TEL NUMBER",
        "TELEPHONE NO",
        "LANDLINE NUMBER",
        "CONTACT NUMBER",
        "TELEPHONE",
        "TEL NO",
        "TEL",
        "CONTACT NO",
        "TELEPHONE NUMBER OF SUPPLIER",
        "BUSINESS TELEPHONE NUMBER",
    ),
    "cell_phone_number": (
        "CELL PHONE NUMBER",
        "CELLPHONE NUMBER",
        "CELL NUMBER",
        "MOBILE NUMBER",
        "CELL PHONE NO",
        "CELL NO",
        "CELLPHONE NO",
        "MOBILE NO",
        "CELL",
        "CELLULAR NUMBER",
    ),
    "fax_number": (
        "FACSIMILE NUMBER",
        "FAX NUMBER",
        "FAX NO",
        "FACSIMILE NO",
    ),
    "email_address": (
        "E-MAIL ADDRESS",
        "EMAIL ADDRESS",
        "E MAIL ADDRESS",
        "ELECTRONIC MAIL ADDRESS",
    ),
    "cidb_registration": (
        "CIDB REGISTRATION NUMBER",
        "CIDB GRADING DESIGNATION",
        "CIDB CONTRACTOR GRADING DESIGNATION",
        "CIDB REGISTRATION NO",
        "CIDB NUMBER",
    ),
    "bid_number": (
        "BID NUMBER",
        "BID NO",
        "TENDER NUMBER",
        "TENDER NO",
        "IN RESPECT OF BID No",
        "QUOTATION NUMBER",
    ),
    "bid_amount": (
        "BID AMOUNT",
        "TENDER AMOUNT",
        "TOTAL BID PRICE",
        "TOTAL TENDERED AMOUNT",
        "OFFER AMOUNT",
        "TOTAL AMOUNT",
    ),
    "identity_number": (
        "IDENTITY NUMBER",
        "ID NUMBER",
        "RSA IDENTITY NUMBER",
        "INDIVIDUAL IDENTITY NUMBER",
        "IDENTITY NUMBER OF BIDDER",
    ),
    "signature": (
        "SIGNATURE",
        "SIGNATURE OF BIDDER",
        "SIGNATURE OF AUTHORISED REPRESENTATIVE",
        "SIGNED AT",
        "SIGNATURE OF TENDERER",
    ),
    "signature_date": (
        "DATE",
        "DATE SIGNED",
        "SIGNATURE DATE",
    ),
    "capacity": (
        "CAPACITY",
        "CAPACITY UNDER WHICH THIS BID IS SIGNED",
        "POSITION",
        "DESIGNATION",
        "POSITION OCCUPIED IN THE COMPANY",
        "POSITION OCCUPIED IN THE ENTERPRISE",
        "POSITION OCCUPIED IN THE COMPANY STRUCTURE",
        # The signature block on MBD 3.1 and most SBDs runs the question
        # THROUGH the blank: "I (full name) ______, in my capacity as ______,
        # the duly authorized representative of ______(company name)". The
        # words left of the second blank are a clause, not a caption, so it
        # matched nothing and the owner filled his own job title in by hand.
        "IN MY CAPACITY AS",
        "IN THE CAPACITY OF",
        "IN MY CAPACITY",
    ),
}


# --------------------------------------------------------------------------
# Labels that must never auto-map, whatever they score
# --------------------------------------------------------------------------
# These are real labels that appear on SA forms but are only disambiguated by a
# surrounding section heading or column header. Mapping them on lexical
# similarity alone is exactly the failure mode that disqualifies a bid.
UNSAFE_GENERIC_LABELS: frozenset[str] = frozenset(
    {
        "REGISTRATION NUMBER",
        "REGISTRATION NO",
        "REGISTRATION",
        "NUMBER",
        "NO",
        "NAME",
        "NAMES",
        "FULL NAME",
        "FULL NAMES",
        "ADDRESS",
        "CODE",
        "REFERENCE NUMBER",
        "STATUS LEVEL",
        "LEVEL",
        "AMOUNT",
        "PIN",
        "TOTAL",
        "OTHER",
        "DETAILS",
        "DESCRIPTION",
        "SUBJECT",
        "YES",
        "NO ",
        "N A",
        "IF YES",
        "IF NO",
        "PARTICULARS",
        "COMMENTS",
        "REMARKS",
    }
)

# Noise tokens stripped before matching. "(IF APPLICABLE)" etc. carry no
# discriminating information but drag fuzzy scores around.
# Words that begin a running sentence, not a field caption.
#
# Applied to the FUZZY path only — never to exact alias hits, so genuine
# captions that legitimately open with a preposition ("IN RESPECT OF BID No")
# still match via the exact index.
#
# The case that forced this: "in the company of (Engineer's representative)"
# normalizes to "IN THE COMPANY OF" and scores WRatio 85.8 / corroboration 88.9
# against the alias "NAME OF THE COMPANY" — it clears both guards. Here
# "company" means *accompanied by*, and the mapping would have written the
# bidder's company name into a site-inspection witness line.
#
# Cost of this rule: an unseeded caption variant that opens with a function word
# is refused rather than fuzzy-matched. That is a safe miss, and the fix is to
# add the variant to ALIAS_SEEDS.
_PROSE_OPENERS: frozenset[str] = frozenset(
    {
        "I", "IN", "OF", "AND", "OR", "THE", "THIS", "THAT", "BY", "AT", "TO",
        "WITH", "FOR", "IF", "AS", "WE", "IS", "ARE", "BE", "HEREBY", "WHO",
        "WHICH", "FROM", "ON", "IT", "ANY", "ALL", "DO", "HAVE", "WAS", "WERE",
    }
)

_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
# Handles "2.1 Name", "2. Name", "a) Name", "iv) Name" — a leading clause
# number carries no field meaning but drags fuzzy scores.
_ENUMERATION_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*[\.\)]?|[a-z][\.\)]|[ivxlc]+[\.\)])\s+", re.IGNORECASE
)
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9 ]+")
_WS_RE = re.compile(r"\s+")
# Leader characters used as fill-in rules on the form itself.
_LEADER_RE = re.compile(r"[_\.…]{2,}")


def normalize_label(raw: str) -> str:
    """Reduce a scraped label to a comparable form.

    Order matters. Leaders are removed before punctuation so that a dotted
    leader does not survive as sentence punctuation; enumeration prefixes are
    removed after unicode folding so "2.1 Name" and "2.1 Name" behave alike.
    """
    if not raw:
        return ""

    text = unicodedata.normalize("NFKD", raw)
    # Drop combining marks and the U+FFFD replacement char that cp1252 mojibake
    # leaves behind in these documents.
    text = "".join(ch for ch in text if not unicodedata.combining(ch) and ch != "�")
    text = text.replace("–", "-").replace("—", "-")
    text = text.replace("’", "'").replace("‘", "'")
    text = _LEADER_RE.sub(" ", text)
    text = text.replace("\n", " ")
    text = _ENUMERATION_RE.sub("", text)
    text = _PARENTHETICAL_RE.sub(" ", text)
    text = text.upper()
    # Keep B-BBEE / E-MAIL joined rather than split into noise tokens.
    text = text.replace("-", "")
    text = _NON_ALNUM_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _build_index() -> tuple[dict[str, str], list[str]]:
    """normalized alias -> canonical, plus the choice list rapidfuzz scores."""
    index: dict[str, str] = {}
    for canonical, variants in ALIAS_SEEDS.items():
        for variant in variants:
            norm = normalize_label(variant)
            if not norm:
                continue
            # First writer wins; seeds are ordered most-canonical-first.
            index.setdefault(norm, canonical)
    return index, list(index.keys())


_ALIAS_INDEX, _CHOICES = _build_index()

_NORMALIZED_UNSAFE = frozenset(normalize_label(x) for x in UNSAFE_GENERIC_LABELS if normalize_label(x))


@dataclass(frozen=True)
class AliasMatch:
    """Outcome of matching one label.

    ``status`` is the field to branch on, not ``canonical``:

    * ``exact``            — normalized label is an alias verbatim. Safe.
    * ``fuzzy``            — above threshold, corroborated, unambiguous. Safe.
    * ``ambiguous``        — scored well for two different canonical fields. NOT
                             safe; ``canonical`` is None, ``runner_up`` says why.
    * ``low_corroboration``— cleared WRatio but failed the second opinion; a
                             partial-match artifact. NOT safe.
    * ``prose_fragment``   — the label opens with a sentence word, so it is a
                             scrap of running text, not a caption. NOT safe.
    * ``blocked``          — label is in the generic blocklist. NOT safe.
    * ``unmatched``        — nothing scored above threshold.

    Only ``exact`` and ``fuzzy`` are safe to draft into; use ``is_confident``.
    """

    raw_label: str
    normalized: str
    canonical: str | None
    score: float
    matched_alias: str | None
    status: str
    runner_up: tuple[str, float] | None = None

    @property
    def is_confident(self) -> bool:
        return self.status in ("exact", "fuzzy")


def _corroboration(normalized: str, alias: str) -> float:
    """Second-opinion score with no partial-substring path."""
    return max(fuzz.token_sort_ratio(normalized, alias), fuzz.ratio(normalized, alias))


def _best_per_canonical(normalized: str) -> tuple[list[tuple[str, str, float]], float]:
    """Ranked (canonical, alias, score), plus the best rejected corroboration.

    Only aliases that clear both ``MATCH_THRESHOLD`` on WRatio *and*
    ``CORROBORATION_FLOOR`` on the second opinion are eligible. The second
    return value is the highest corroboration seen among aliases that passed
    WRatio but failed corroboration, so the caller can distinguish "nothing
    looked similar" from "something looked similar but did not hold up".
    """
    scored = process.extract(
        normalized,
        _CHOICES,
        scorer=fuzz.WRatio,
        limit=None,
        processor=None,
    )
    best: dict[str, tuple[str, float]] = {}
    best_rejected = 0.0

    for alias, score, _ in scored:
        score = float(score)
        if score < MATCH_THRESHOLD:
            continue
        corroboration = _corroboration(normalized, alias)
        if corroboration < CORROBORATION_FLOOR:
            best_rejected = max(best_rejected, score)
            continue
        canonical = _ALIAS_INDEX[alias]
        if canonical not in best or score > best[canonical][1]:
            best[canonical] = (alias, score)

    rows = [(c, a, s) for c, (a, s) in best.items()]
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows, best_rejected


def match_label(raw_label: str | None, threshold: float = MATCH_THRESHOLD) -> AliasMatch:
    """Map one scraped label to a canonical field, or explain why it cannot be."""
    raw = (raw_label or "").strip()
    normalized = normalize_label(raw)

    if not normalized:
        return AliasMatch(raw, normalized, None, 0.0, None, "unmatched")

    if normalized in _NORMALIZED_UNSAFE:
        return AliasMatch(raw, normalized, None, 0.0, None, "blocked")

    if normalized in _ALIAS_INDEX:
        return AliasMatch(
            raw, normalized, _ALIAS_INDEX[normalized], 100.0, normalized, "exact"
        )

    # Past this point only fuzzy matching remains, so a sentence opener means
    # the "label" is a fragment of running prose, not a caption.
    first_token = normalized.split(" ", 1)[0]
    if first_token in _PROSE_OPENERS:
        return AliasMatch(raw, normalized, None, 0.0, None, "prose_fragment")

    ranked, best_rejected = _best_per_canonical(normalized)
    if not ranked:
        if best_rejected:
            # Something scored above threshold but failed the second opinion —
            # almost always a WRatio partial-match artifact against prose.
            return AliasMatch(
                raw, normalized, None, best_rejected, None, "low_corroboration"
            )
        return AliasMatch(raw, normalized, None, 0.0, None, "unmatched")

    top_canonical, top_alias, top_score = ranked[0]
    if top_score < threshold:
        return AliasMatch(raw, normalized, None, top_score, top_alias, "unmatched")

    if len(ranked) > 1:
        second_canonical, _, second_score = ranked[1]
        if top_score - second_score < AMBIGUITY_MARGIN:
            return AliasMatch(
                raw,
                normalized,
                None,
                top_score,
                top_alias,
                "ambiguous",
                runner_up=(second_canonical, second_score),
            )

    runner = (ranked[1][0], ranked[1][2]) if len(ranked) > 1 else None
    return AliasMatch(raw, normalized, top_canonical, top_score, top_alias, "fuzzy", runner)


def match_labels(labels: Iterable[str | None]) -> list[AliasMatch]:
    return [match_label(label) for label in labels]


def alias_count() -> int:
    return len(_ALIAS_INDEX)
