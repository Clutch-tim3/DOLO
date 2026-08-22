"""
Which SBD forms a pack contains, and which edition of each.

`SBD_COMPLIANCE.md` P1-5, and the fourth of the training guide's four golden
rules: "Never use old forms — always use the exact forms from the tender pack."

THE CHANGE THAT MATTERS

    CRITICAL UPDATE (Effective 31 March 2022): The new SBD 4: Bidder's
    Disclosure replaced the old SBD 4 (Declaration of Interest), SBD 8 (Past
    SCM Practices), and SBD 9 (Independent Bid Determination) into one
    consolidated form. However, some departments still use the old separate
    forms — always use the exact forms from the tender pack.

So "SBD 4" names two different documents. The pre-2022 one is a declaration of
interest only; the consolidated one also carries past SCM practices and the
independent-bid certification. A pack containing SBD 8 or SBD 9 is telling you
it uses the old three, and answering it as though it were the new one leaves
two thirds of the disclosure unmade.

WHAT THIS IS FOR, AND WHAT IT IS NOT FOR

It reports. Nothing here changes a fill decision, and it must not: substituting
a form from another pack is the exact failure golden rule 4 names. The value is
telling a person which forms are in front of them and which edition, so they
can check the pack is complete and current before they start.

READ FROM THE PACK'S OWN TEXT

Never from a stored list of what SBD 4 "should" contain. The tender's own
version is the only correct one, and a pack that disagrees with National
Treasury's current template is still the pack that has to be submitted.
"""

from __future__ import annotations

import re

#: "SBD 6.1", "MBD 3.2", "SBD 4". Municipalities issue MBD; the numbering and
#: the content are the same, so both are recognised and reported as found.
_FORM = re.compile(r"\b(SBD|MBD)\s*[-–]?\s*(\d{1,2}(?:\.\d)?)\b", re.I)

#: Wording unique to the consolidated form. "Bidder's Disclosure" is its title;
#: the other two are section headings that only exist once SBD 8 and SBD 9 were
#: folded in.
_CONSOLIDATED_MARKERS = (
    re.compile(r"bidder.?s\s+disclosure", re.I),
    re.compile(r"declaration\s+of\s+past\s+scm\s+practices", re.I),
    re.compile(r"certificate\s+of\s+independent\s+bid\s+determination", re.I),
)

#: The pre-2022 SBD 4's own title.
_LEGACY_SBD4 = re.compile(r"declaration\s+of\s+interest", re.I)

CONSOLIDATED = "consolidated (post-31 March 2022)"
LEGACY = "legacy (pre-31 March 2022)"
UNKNOWN = "edition not stated"


def forms_in(text: str) -> dict:
    """
    {"SBD 4": {"first_seen": 37, ...}} for every form named in the pack.

    Page numbers are 1-based, for a person holding the document.
    """
    found: dict[str, dict] = {}
    for page_index, page_text in enumerate(text.split("\f")):
        for match in _FORM.finditer(page_text):
            name = f"{match.group(1).upper()} {match.group(2)}"
            found.setdefault(name, {"form": name, "first_seen": page_index + 1})
    return found


def sbd4_edition(text: str) -> str:
    """
    Which SBD 4 this pack contains.

    The consolidated markers are checked first and win. A pack can carry the
    words "declaration of interest" inside the new form — Part A is still
    titled that — so finding them proves nothing on its own, while the past-SCM
    and independent-bid headings exist only in the consolidated edition.
    """
    body = text or ""
    if any(marker.search(body) for marker in _CONSOLIDATED_MARKERS):
        return CONSOLIDATED
    if _LEGACY_SBD4.search(body):
        return LEGACY
    return UNKNOWN


def describe_pack(text: str) -> dict:
    """
    What forms are in this pack, which SBD 4 edition, and what to watch for.

    `notes` is what a person should act on. Empty means nothing unusual — not
    that the pack is complete, which only the tender's own returnable-documents
    list can say.
    """
    body = text or ""
    found = forms_in(body)
    edition = sbd4_edition(body)
    names = set(found)

    notes = []
    has_legacy_pair = bool(names & {"SBD 8", "SBD 9", "MBD 8", "MBD 9"})

    # The mixed-edition case is more specific and says everything this one
    # would, so they are exclusive rather than both firing.
    if has_legacy_pair and edition != CONSOLIDATED:
        notes.append(
            "This pack contains SBD 8 and/or SBD 9, the separate pre-2022 "
            "forms. They were consolidated into SBD 4 on 31 March 2022, but "
            "some departments still issue them — complete the forms that are "
            "in the pack, not the current template."
        )

    if edition == LEGACY and not has_legacy_pair:
        notes.append(
            "The SBD 4 in this pack is the pre-2022 Declaration of Interest, "
            "which covers conflicts of interest only. The consolidated form "
            "also requires past SCM practices and an independent-bid "
            "certification — if the pack asks for those separately, look for "
            "SBD 8 and SBD 9."
        )

    if edition == CONSOLIDATED and has_legacy_pair:
        notes.append(
            "This pack mixes editions: it has the consolidated SBD 4 AND the "
            "separate SBD 8/9 it replaced. Complete every form the pack "
            "includes — a returnable document left out is a disqualification, "
            "and duplication is not."
        )

    return {
        "forms": sorted(found.values(), key=lambda f: f["first_seen"]),
        "form_names": sorted(names),
        "sbd4_edition": edition if "SBD 4" in names or "MBD 4" in names else None,
        "notes": notes,
    }
