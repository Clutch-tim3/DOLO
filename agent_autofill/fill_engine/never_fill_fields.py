"""
The blocklist. This is the most safety-critical module in Agent Autofill.

Agent Autofill produces DRAFTS ONLY. Everything here is a field the engine must
NEVER write into a document, no matter how confidently it was matched and no
matter what the company profile happens to hold. A field landing here is left
blank, marked in the document, and reported in the review summary as requiring
a human.

Why each category is blocked
---------------------------
SIGNATURE      Applying a signature on someone's behalf is forgery. There is no
               threshold of confidence that makes it acceptable.
DECLARATION    SBD 4 / MBD 4 state-employee declarations. Even when a prior
               document recorded an answer, the answer is per-tender and per-
               date. Reusing a stored "no" silently is how a company ends up
               having made a false declaration to an organ of state.
PRICING        Any money figure routes through the existing quotation review
               gate, which has its own audit trail. This engine never writes a
               price.
NARRATIVE      Method statements, motivations, "describe your approach". The
               agent may DRAFT these into the review summary as a suggestion,
               but inserting generated prose into a submitted bid without the
               bidder having written it misrepresents authorship.
SIGNING_DATE   The date a human signed. Filling it asserts that signing
               happened on a date the engine cannot know.

Matching is deliberately over-inclusive. A false positive costs the user one
manual entry. A false negative can forge a signature or make a false
declaration. When in doubt, block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class BlockReason(str, Enum):
    SIGNATURE = "signature"
    DECLARATION = "declaration_of_interest"
    PRICING = "pricing"
    NARRATIVE = "narrative"
    SIGNING_DATE = "signing_date"


#: Human-facing explanation shown in the review summary for each reason.
BLOCK_MESSAGES: dict[BlockReason, str] = {
    BlockReason.SIGNATURE: (
        "Requires your signature. Agent Autofill never signs anything on your behalf."
    ),
    BlockReason.DECLARATION: (
        "Declaration of interest — must be answered for THIS tender. A previous "
        "answer is not carried over, because the declaration is made afresh each time."
    ),
    BlockReason.PRICING: (
        "Pricing must come from the quotation system, which keeps its own audit "
        "trail. Agent Autofill never writes a figure into a bid."
    ),
    BlockReason.NARRATIVE: (
        "Free-text response. Agent can draft wording for you to review separately, "
        "but will not insert prose into the document as if you had written it."
    ),
    BlockReason.SIGNING_DATE: (
        "Date of signing — fill this in when you actually sign."
    ),
}


def _rx(*parts: str) -> re.Pattern:
    return re.compile("|".join(parts), re.IGNORECASE)


# --- exemptions ------------------------------------------------------------
# Labels that mention a blocked word but are asking for a plain fact. Checked
# BEFORE the block rules, so they must stay narrow and specific — an exemption
# is the one place in this module where a mistake unblocks something.
#
# Both of these were found against the real MBD 1 supplier block:
#   "CAPACITY UNDER WHICH THIS BID IS SIGNED" was blocked as a signature
#   because of the word "SIGNED". Capacity is "Director" — a fact about who is
#   signing, not the signature itself.
#   "TOTAL NUMBER OF ITEMS OFFERED" was blocked as pricing because of a greedy
#   \btotal\b. It is a count, not money.
EXEMPT_PATTERNS = _rx(
    # capacity / designation: who the signatory is, not their mark
    r"^\s*capacity\b(?!.*\bsignature\b)",
    r"capacity\s+under\s+which",
    r"capacity\s+in\s+which",
    r"^\s*designation\b(?!.*\bsignature\b)",
    r"^\s*position\s+held\b",
    # counts and quantities that happen to contain a money-ish word
    r"total\s+(?:number|no\.?|count|quantity|qty)\b",
    r"number\s+of\s+items\b",
    r"total\s+items\b",
)


def _is_exempt(text: str) -> bool:
    """True if a blocked-looking label is actually asking for a plain fact."""
    return bool(EXEMPT_PATTERNS.search(text))


# --- counterparty sections -------------------------------------------------
# MBD 1 carries "CONTACT PERSON", "TELEPHONE NUMBER" and "E-MAIL ADDRESS" TWICE:
# once under "BIDDING PROCEDURE ENQUIRIES MAY BE DIRECTED TO" — the buying
# institution's own staff, already printed on the form — and again under
# "SUPPLIER INFORMATION", which is us. The labels are identical, so without the
# section heading the engine cannot tell them apart and would write our contact
# details into the municipality's block.
COUNTERPARTY_SECTION_PATTERNS = _rx(
    r"directed\s+to",
    r"enquir(?:y|ies)",
    r"technical\s+enquiries",
    r"for\s+(?:the\s+)?attention\s+of",
    r"procuring\s+institution",
    r"department\b",
    r"issued\s+by",
    r"on\s+behalf\s+of\s+the\s+(?:municipality|department|entity)",
)


def is_counterparty_section(section: str | None) -> bool:
    """True if a heading marks a block belonging to the buyer, not the bidder."""
    return bool(section and COUNTERPARTY_SECTION_PATTERNS.search(section))


# --- signature -------------------------------------------------------------
# Covers the obvious ("SIGNATURE"), the imperative ("Sign here", "Please sign
# below"), the witness/deponent variants that appear on affidavits and SBD
# forms, and the initialling boxes SA tender packs put on every page.
SIGNATURE_PATTERNS = _rx(
    r"\bsignature[sd]?\b",
    r"\bsigned\b",
    r"\bsign(?:ing)?\s+(?:here|below|above|off)\b",
    r"\bplease\s+sign\b",
    r"\bsign\s*:",
    r"^\s*sign\b",
    r"\bsignatory\b",
    r"\bhandtekening\b",          # Afrikaans — SA forms are frequently bilingual
    r"\bwitness(?:es)?\b",
    r"\bdeponent\b",
    r"\bcommissioner\s+of\s+oaths\b",
    r"\binitial(?:s|led)?\b",
    r"\bduly\s+authorised\s+signatory\b",
)

# --- declaration of interest ----------------------------------------------
DECLARATION_PATTERNS = _rx(
    r"declaration\s+of\s+interest",
    r"\bmbd\s*4\b",
    r"\bsbd\s*4\b",
    r"employed\s+by\s+the\s+state",
    r"\bstate\s+employee\b",
    r"in\s+the\s+service\s+of\s+the\s+state",
    r"\bpublic\s+office\s+bearer\b",
    r"connected\s+(?:to|with)\s+any\s+person\s+employed",
    r"relationship\s+with\s+(?:any\s+)?person(?:s)?\s+employed",
    r"\bconflict\s+of\s+interest\b",
    r"family\s+member.*employed",
)

# --- pricing ---------------------------------------------------------------
PRICING_PATTERNS = _rx(
    r"\bprice\b", r"\bpricing\b", r"\bamount\b", r"\bcost\b", r"\bfee\b",
    r"\brate\b", r"\btariff\b", r"\bquotation\b", r"\bquote\b",
    r"\btotal\b", r"\bsubtotal\b", r"\bsum\b",
    r"\bvat\s+(?:amount|inclusive|exclusive)\b",
    r"\bzar\b", r"\bbid\s+amount\b", r"\bbid\s+price\b",
    r"\bcontract\s+(?:value|sum|price)\b",
    r"\bR\s*[_.]{3,}",            # "R ______" money blank
    r"\brand\s+value\b",
)

# --- narrative -------------------------------------------------------------
NARRATIVE_PATTERNS = _rx(
    r"method\s+statement",
    r"\bmotivat(?:ion|e)\b",
    r"describe\s+your\b",
    r"\bdescription\s+of\s+(?:your|the)\s+(?:approach|methodology|experience)\b",
    r"\bapproach\s+and\s+methodology\b",
    r"\bplease\s+(?:explain|elaborate|describe|specify\s+in\s+detail)\b",
    r"\bproject\s+plan\b",
    r"\bexecutive\s+summary\b",
    r"\breasons?\s+(?:for|why)\b",
    r"\bcomments?\b",
)

# --- signing date ----------------------------------------------------------
# Only a date tied to the act of signing is blocked. A closing date or a
# briefing date is factual and may be filled.
SIGNING_DATE_PATTERNS = _rx(
    r"date\s+(?:of\s+)?sign(?:ing|ature|ed)",
    r"signed\s+(?:on|at|this)\b",
    r"\bthus\s+(?:done\s+and\s+)?signed\b",
    r"dated?\s+at\b",
    r"\bthis\s+\d*\s*_*\s*day\s+of\b",
    r"\(day\)\s*of",
)

_ORDERED_RULES: list[tuple[BlockReason, re.Pattern]] = [
    # Signature first: "signed at ... on ... date" is a signature context, and
    # calling it a date would understate why it is blocked.
    (BlockReason.SIGNATURE, SIGNATURE_PATTERNS),
    (BlockReason.DECLARATION, DECLARATION_PATTERNS),
    (BlockReason.SIGNING_DATE, SIGNING_DATE_PATTERNS),
    (BlockReason.PRICING, PRICING_PATTERNS),
    (BlockReason.NARRATIVE, NARRATIVE_PATTERNS),
]

#: Canonical field names that are blocked regardless of the label text that
#: produced them. Belt and braces: if the alias dictionary ever maps a label to
#: one of these, it is still refused.
BLOCKED_CANONICAL_FIELDS: dict[str, BlockReason] = {
    "signature": BlockReason.SIGNATURE,
    "signature_date": BlockReason.SIGNING_DATE,
    "bid_amount": BlockReason.PRICING,
}


# --- document-level context ------------------------------------------------
# Per-label matching is not sufficient, and this was found against the real
# SBD 4 Annexure A. That form's declaration table is a grid of innocuous-looking
# cells — "Full Name", "Identity Number", "Name of organ of state" — repeated
# for each person connected to the bidder who is employed by the state.
#
# None of those labels trip the declaration patterns on their own. Today they
# survive only because the alias dictionary happens not to map "Full Name"; add
# that obvious alias and the engine would auto-populate a state-employee
# declaration from stored director data. That is a false declaration to an organ
# of state, produced silently.
#
# So the whole document is classified first, and inside a declaration form the
# person-identifying fields are blocked regardless of their label.

# Titles matter here, and the obvious one is out of date. The current National
# Treasury form is headed "BIDDER'S DISCLOSURE" — "Declaration of Interest" is
# the older MBD 4 wording. Keying only on the old title misses every revised
# form in circulation, which is most of them. Verified against
# tests/fixtures/sa_forms/REVISED SBD 4 -Annexure A.docx.
DECLARATION_DOCUMENT_MARKERS = _rx(
    r"declaration\s+of\s+interest",
    r"bidder.{0,3}s\s+disclosure",                       # curly or straight apostrophe
    r"declaration\s+on\s+employment\s+by\s+organ\s+of\s+state",
    r"general\s+declaration",
    r"\b[SM]BD\s*4\b",
    r"annexure\s+a\b.*declaration",
    r"in\s+the\s+service\s+of\s+the\s+state",
    r"employed\s+by\s+the\s+state",
    r"bid\s+is\s+not\s+in\s+conflict",
    r"register\s+for\s+tender\s+defaulters",
    r"list\s+of\s+restricted\s+suppliers",
)

#: Inside a declaration document these labels identify a PERSON being declared,
#: not a fact about our own company.
DECLARATION_CONTEXT_LABELS = _rx(
    r"full\s+name",
    r"\bidentity\s+number\b",
    r"\bid\s+number\b",
    r"name\s+of\s+organ\s+of\s+state",
    r"\bposition\s+(?:held|occupied)\b",
    r"supplier\s+registration\s+number",
    r"\bstatus\b",
    r"\bname\b",
)


def classify_document_context(full_text: str | None) -> set[str]:
    """
    Identify what kind of form this is, so per-label decisions can be made with
    the document in mind. Returns a set of context tags.
    """
    tags: set[str] = set()
    if not full_text:
        return tags
    if DECLARATION_DOCUMENT_MARKERS.search(full_text):
        tags.add("declaration_of_interest")
    return tags


@dataclass(frozen=True)
class BlockDecision:
    """Why a field may not be auto-filled."""

    blocked: bool
    reason: BlockReason | None = None
    message: str | None = None
    matched_on: str | None = None

    def __bool__(self) -> bool:      # `if is_blocked(...)`
        return self.blocked


ALLOW = BlockDecision(blocked=False)


def is_blocked(label_text: str | None, canonical_field: str | None = None,
               context: set[str] | None = None,
               section: str | None = None) -> BlockDecision:
    """
    Decide whether a detected field may be auto-filled.

    Checked in this order so the most serious reason wins:
      1. canonical field on the hard blocklist
      2. document context (e.g. every person-field inside an SBD 4)
      3. label text against the ordered pattern rules

    An unreadable or empty label is BLOCKED, not allowed. If extraction could
    not tell us what a blank is for, the engine has no business writing into it.

    `context` comes from classify_document_context() over the whole document.
    """
    if canonical_field and canonical_field in BLOCKED_CANONICAL_FIELDS:
        reason = BLOCKED_CANONICAL_FIELDS[canonical_field]
        return BlockDecision(True, reason, BLOCK_MESSAGES[reason], f"canonical:{canonical_field}")

    text = (label_text or "").strip()
    if not text:
        return BlockDecision(
            True,
            BlockReason.NARRATIVE,
            "Could not read what this field is for, so it was left for you to complete.",
            "empty-label",
        )

    # A field inside the buying institution's own block is not ours to fill,
    # however well its label matches.
    if is_counterparty_section(section):
        return BlockDecision(
            True,
            BlockReason.NARRATIVE,
            f"Belongs to the buying institution ('{section.strip()[:48]}'), not to you.",
            f"counterparty-section:{section.strip()[:32]}",
        )

    # A narrowly-exempt label (capacity, item counts) is a plain fact even
    # though it contains a blocked word. Declaration context still wins below —
    # exemptions never override a declaration form.
    exempt = _is_exempt(text)

    # Inside a declaration form, a person-identifying cell is part of the
    # declaration itself even though its label reads innocuously.
    if context and "declaration_of_interest" in context:
        m = DECLARATION_CONTEXT_LABELS.search(text)
        if m:
            return BlockDecision(
                True,
                BlockReason.DECLARATION,
                BLOCK_MESSAGES[BlockReason.DECLARATION],
                f"declaration-context:{m.group(0)}",
            )

    for reason, pattern in _ORDERED_RULES:
        m = pattern.search(text)
        if m:
            # Signature and pricing are the two categories whose vocabulary
            # bleeds into ordinary factual labels, so they are the only ones an
            # exemption can clear. Declarations and narrative are never exempt.
            if exempt and reason in (BlockReason.SIGNATURE, BlockReason.PRICING):
                continue
            return BlockDecision(True, reason, BLOCK_MESSAGES[reason], m.group(0))

    return ALLOW
