"""
Which procurement system a document belongs to, and the rules that differ.

CairoAI was built for South African government tenders and applies their rules
to everything it opens. `Comprehensive_Tender_Document_Training_Guide.pdf`
covers two more systems its Part 4 table sets side by side, and the columns do
not agree.

WHY THIS IS THE FIRST PIECE

Not "support UNGM and World Bank" — recognising which system is in front of you
comes before any of that, because the SA assumptions are actively wrong
elsewhere and CairoAI states them confidently. A UN ITB has no CSD number, no
B-BBEE level, no CIDB grade and no COIDA letter; a bidder told to check theirs
is being sent to look for something that does not exist in that tender.

THE TWO DIFFERENCES THAT CAN COST A BID

    Price in technical bid   SA:  combined, price visible
                             UN:  MUST NOT appear in the technical part
                             WB:  MUST NOT appear in the technical part

A bidder used to SA packs, where the price sits on SBD 3 inside the same
submission, will put a figure in a UN technical proposal and be disqualified for
it before anything is evaluated. CairoAI never writes a price, so it cannot
cause this — but it is the single most expensive habit to carry across, and
saying so costs nothing.

    Alterations              SA:  must be initialled by the signatory
                             UN:  no deletion or modification permitted
                             WB:  must be initialled by the signatory

Filling a blank the form provides is not an alteration under any of the three —
that is completing the document as its author intended. Changing pre-printed
text is, and CairoAI does not do that: `never_fill_fields` refuses, and
`pdf_filler` writes only into detected blanks. The rule is reported because the
person adding the remaining answers by hand needs it, not because the fill
breaks it.

IT REPORTS AND NOTHING MORE

No fill decision reads this. A misdetection therefore costs a wrong note on a
review screen, which is visible, and never a wrong value on a form.
"""

from __future__ import annotations

import re

SOUTH_AFRICA = "south_africa"
UNGM = "ungm"
WORLD_BANK = "world_bank"
UNKNOWN = "unknown"

#: Each pattern is specific to one system. Deliberately no generic procurement
#: vocabulary — "bidder", "tender" and "procurement" appear in all three, and a
#: match on those would mean nothing.
_MARKERS = {
    SOUTH_AFRICA: (
        re.compile(r"\b[SM]BD\s*\d", re.I),
        re.compile(r"central\s+supplier\s+database|\bCSD\s+(number|supplier)", re.I),
        re.compile(r"\bB-?BBEE\b|preferential\s+procurement\s+policy\s+framework", re.I),
        re.compile(r"\bCIDB\b|\bCOIDA\b|national\s+treasury", re.I),
        re.compile(r"\bPFMA\b|\bMFMA\b", re.I),
    ),
    UNGM: (
        re.compile(r"\bUNGM\b|united\s+nations\s+global\s+marketplace", re.I),
        re.compile(r"\bUNSPSC\b", re.I),
        re.compile(r"united\s+nations\s+(development\s+programme|children)", re.I),
        re.compile(r"\bUNDP\b|\bUNICEF\b|\bUNOPS\b|\bUNHCR\b", re.I),
    ),
    WORLD_BANK: (
        re.compile(r"world\s+bank|\bIBRD\b|\bIDA\b", re.I),
        re.compile(r"standard\s+procurement\s+document", re.I),
        re.compile(r"\bESHS\b|environmental,?\s+social,?\s+health", re.I),
        re.compile(r"beneficial\s+ownership\s+(form|disclosure)", re.I),
        re.compile(r"\bBDS\b.{0,40}bid\s+data\s+sheet|bid\s+data\s+sheet", re.I),
    ),
}

#: What CairoAI knows how to do, per system. Being honest about this is the
#: point: the fill engine's field vocabulary, its blocklist and its compliance
#: checks are all built around SBD forms.
SUPPORT = {
    SOUTH_AFRICA: "full",
    UNGM: "recognised_only",
    WORLD_BANK: "recognised_only",
    UNKNOWN: "recognised_only",
}

_NOT_SOUTH_AFRICAN = (
    "CairoAI's form knowledge is South African — SBD and MBD forms, CSD, "
    "B-BBEE, CIDB, COIDA. It can read this document and flag what it "
    "recognises, but it does not know this system's forms, so treat anything "
    "it fills here as a starting point and check every field yourself."
)

RULES = {
    UNGM: [
        "PRICE MUST NOT APPEAR IN THE TECHNICAL PART. UN bids are evaluated in "
        "two envelopes and a figure in the technical proposal is a "
        "disqualification before anything is read. This is the habit most "
        "likely to carry over from a South African pack, where the price sits "
        "on SBD 3 inside the same submission.",
        "No deletion or modification of the issued document is permitted. "
        "Complete the blanks it provides and attach anything else separately.",
        "Your company must already be registered on UNGM at the level that "
        "covers the contract value — Basic under USD 150 000, Level 1 to USD "
        "500 000, Level 2 above it. Registration is not pre-qualification.",
        "Every supporting document must be a PDF.",
    ],
    WORLD_BANK: [
        "PRICE MUST NOT APPEAR IN THE TECHNICAL PART. Large works use two "
        "envelopes and a figure in the technical proposal disqualifies the bid.",
        "The Bid Data Sheet overrides the Instructions to Bidders. Every number "
        "that governs your bid — validity, securities, currencies — is in the "
        "BDS, not the general conditions.",
        "Alterations must be initialled by the authorised signatory.",
        "A signatory needs written authority: a power of attorney, not just a "
        "job title.",
    ],
    SOUTH_AFRICA: [
        "Alterations must be initialled by the authorised signatory.",
        "The SBD 1 total must match the SBD 3 grand total exactly.",
    ],
}


def detect(text: str) -> dict:
    """
    Which system this document is from, with the evidence and the score.

    Scored rather than first-match: procurement documents quote each other, and
    a World Bank-funded South African tender genuinely contains both
    vocabularies. The highest count wins and the runners-up are reported, so a
    close call is visible instead of silently resolved.
    """
    body = text or ""
    scores: dict[str, list[str]] = {}

    for system, patterns in _MARKERS.items():
        hits = [p.pattern for p in patterns if p.search(body)]
        if hits:
            scores[system] = hits

    if not scores:
        return {"system": UNKNOWN, "confidence": 0.0, "evidence": [],
                "also_matched": [], "support": SUPPORT[UNKNOWN],
                "rules": [], "notes": [_NOT_SOUTH_AFRICAN]}

    ranked = sorted(scores.items(), key=lambda kv: len(kv[1]), reverse=True)
    system, evidence = ranked[0]
    total = sum(len(v) for v in scores.values())

    notes = []
    if system != SOUTH_AFRICA:
        notes.append(_NOT_SOUTH_AFRICAN)
    if len(ranked) > 1:
        notes.append(
            "This document carries the vocabulary of more than one procurement "
            "system: " + ", ".join(s for s, _ in ranked) + ". A donor-funded "
            "tender can genuinely be both — the rules that apply are the ones "
            "in this tender's own instructions."
        )

    return {
        "system": system,
        "confidence": round(len(evidence) / total, 2) if total else 0.0,
        "evidence": evidence,
        "also_matched": [s for s, _ in ranked[1:]],
        "support": SUPPORT[system],
        "rules": RULES.get(system, []),
        "notes": notes,
    }
