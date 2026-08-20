"""
Why a field was left, said in words that match what actually happened.

WHY THIS EXISTS
---------------
Every refusal that was not a block came back as one category and one sentence:

    unmatched — "I could not tell what this field is asking for."

On a real RFQ that sentence appeared 41 times, and the owner read it as the
system failing to understand his form. It mostly wasn't. Of those 41:

    22   SBD 4 declaration fields — "Full Name", "Name of State institution",
         "If so, furnish particulars". A sworn declaration of interest that
         only the bidder can make, which the system is right to refuse.
     8   Bare "R" cells in a pricing table. Price columns, not questions.
     5   "Packaging Charge", "Lead Time for delivery", "Delivery Charge to
         iThemba LABS" — commercial terms for THIS bid, which no company
         profile could ever hold.
     2   A field labelled only "ADDRESS", genuinely ambiguous between postal
         and physical.
     3   Actual dictionary misses.

Three of forty-one were the failure the message described. The rest were the
system working correctly and describing itself as confused — which is worse
than a bad refusal, because it teaches the user to distrust good ones.

WHAT THIS DOES NOT DO
---------------------
Change a single decision. Nothing here fills a field that was refused, or
refuses one that was filled. `never_fill_fields` and `safe_fill_fields` still
decide; this only explains. A wrong explanation is a bug worth fixing, and a
right one must never become a reason to fill.
"""

from __future__ import annotations

import re

#: A cell that is a currency marker rather than a question. On the owner's RFQ
#: eight cells carried nothing but "R" — the rand column of a pricing table.
_CURRENCY_ONLY = re.compile(r"^[Rr]\s*[:.]?$")

#: Wording that only appears on a declaration the bidder swears to. These are
#: SBD 4 territory: the declaration of interest, where a wrong answer is not a
#: typo but a false declaration to an organ of state.
_DECLARATION_MARKERS = (
    "STATE INSTITUTION",
    "FURNISH PARTICULARS",
    "IF SO",
    "IN THE SERVICE OF THE STATE",
    "SURNAME AND NAME",
    "IDENTITY NUMBER",
    "ID NUMBER",
    "SHAREHOLDER",
    "DIRECTOR",
    "MEMBER",
    "RELATIONSHIP",
    "SPOUSE",
)

#: Commercial terms a tender asks about THIS bid. They are legitimate questions
#: with real answers — the answers just are not facts about the company, so no
#: profile could ever hold them.
_TENDER_TERM_MARKERS = (
    "LEAD TIME",
    "DELIVERY",
    "PACKAGING",
    "CHARGE",
    "WARRANTY",
    "GUARANTEE",
    "VALIDITY",
    "DISCOUNT",
    "AVAILABILITY",
    "TURNAROUND",
    "COMPLETION",
)

#: Category -> the sentence the user reads. Written so a correct refusal reads
#: as a decision the system made on purpose, not as a shrug.
REASONS = {
    "declaration": (
        "This is part of a sworn declaration. Only you can answer it, and "
        "CairoAI will not answer it for you."
    ),
    "pricing_cell": (
        "This is a price cell. Figures come from the quotation, which keeps "
        "its own audit trail."
    ),
    "tender_terms": (
        "This tender is asking for your terms on this bid — not a fact about "
        "your company, so there is nothing on file to fill it from."
    ),
    "ambiguous_label": (
        "The label is too general to answer safely. Tell me which value it "
        "wants and I will fill it."
    ),
    "prose": (
        "This is a sentence from the form, not a field."
    ),
    "unmatched": (
        "I could not tell what this field is asking for."
    ),
}

#: Categories that are the system working correctly rather than failing. The
#: review screen can present these as information instead of as problems.
CORRECT_BY_DESIGN = frozenset({"declaration", "pricing_cell", "tender_terms"})


def classify_unfilled(label: str | None, match=None) -> tuple[str, str]:
    """
    (category, reason) for a field with no canonical match.

    `match` is the `AliasMatch`, whose `status` already distinguishes an unsafe
    label from a prose fragment from a near miss — information the caller used
    to throw away by collapsing everything into "unmatched".

    Order matters. A declaration is checked before anything else because
    getting that one wrong is the difference between a refusal and a false
    sworn statement.
    """
    text = (label or "").strip()
    upper = text.upper()

    if not text:
        return "unmatched", REASONS["unmatched"]

    if any(marker in upper for marker in _DECLARATION_MARKERS):
        return "declaration", REASONS["declaration"]

    if _CURRENCY_ONLY.match(text):
        return "pricing_cell", REASONS["pricing_cell"]

    if any(marker in upper for marker in _TENDER_TERM_MARKERS):
        return "tender_terms", REASONS["tender_terms"]

    status = getattr(match, "status", None) if match is not None else None

    if status == "prose_fragment":
        return "prose", REASONS["prose"]

    # `blocked` here is the alias dictionary's unsafe list — bare NAME, ADDRESS,
    # AMOUNT, CODE — not `never_fill_fields`. The label is real but too general
    # to resolve, which is a question for the user rather than a failure.
    if status in ("blocked", "ambiguous"):
        return "ambiguous_label", REASONS["ambiguous_label"]

    return "unmatched", REASONS["unmatched"]


def summarise(skipped) -> dict[str, int]:
    """How many of each category, for a caller that wants to lead with counts."""
    counts: dict[str, int] = {}
    for item in skipped:
        category = getattr(item, "category", None) or "unmatched"
        counts[category] = counts.get(category, 0) + 1
    return counts
