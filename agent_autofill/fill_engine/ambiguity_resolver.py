"""
Resolving a label that is only ambiguous in theory.

WHY THIS EXISTS
---------------
Some labels are too general to map to one field: a cell saying only "ADDRESS"
could want the postal or the physical one, and guessing puts a wrong answer on
a bid. So `field_alias_dictionary` keeps those on an unsafe list and refuses
them, which is right in principle.

In practice it was refusing fields it had exactly one possible answer for. The
owner's company has the same postal and physical address — the string is
character-for-character identical — so "ADDRESS" had no ambiguity to protect
him from, and the form came back with an empty address line and a note
explaining that CairoAI could not tell which one was wanted. Both. Either.
They are the same.

His words: "I want it to fill everything that its legal to fill."

WHAT THIS DOES
--------------
For a label whose meaning is genuinely uncertain, look at the candidate values
the company actually has. If they all agree, or only one of them is set, the
uncertainty is theoretical and the field is filled. If they genuinely differ,
nothing is filled and the user is asked — which is the case the unsafe list
exists for.

This never widens what may be written. Every candidate is a field already on
`SAFE_FILL_FIELDS`, and `never_fill_fields` still runs first and unconditionally.
A signature line reading "NAME" is refused here exactly as it was before.
"""

from __future__ import annotations

#: Ambiguous label -> the profile columns it might mean, in preference order.
#: Only columns that are already safe to fill appear here; this resolves which
#: of several permitted answers applies, never whether a field may be answered.
AMBIGUOUS_GROUPS: dict[str, tuple[str, ...]] = {
    "ADDRESS": ("physical_address", "postal_address"),
    "ADDRESS OF": ("physical_address", "postal_address"),
    "COMPANY DETAILS": ("company_name",),
    "CONTACT DETAILS": ("standard_phone", "standard_cell"),
    "CONTACT": ("standard_phone", "standard_cell"),
    "TELEPHONE": ("standard_phone", "standard_cell"),
    "NUMBER": (),          # deliberately empty: could be any of a dozen fields
    "NAME": (),            # bidder, director, referee, account holder
    "FULL NAME": (),
    "FULL NAMES": (),
}


def _normalise(label: str | None) -> str:
    return " ".join((label or "").upper().split()).strip(" :.-")


def resolve(label: str | None, profile: dict) -> tuple[str | None, str | None]:
    """
    (canonical_field, value) for an ambiguous label, or (None, None).

    Returns a field only when the answer is not in doubt:

      * every candidate the company has holds the same value, or
      * exactly one candidate is populated.

    Two different addresses means two different answers, and only the user
    knows which the form wants — so that returns (None, None) and the field
    goes to them as a question.
    """
    candidates = AMBIGUOUS_GROUPS.get(_normalise(label))
    if not candidates:
        return None, None

    present = [
        (field, str(profile.get(field)).strip())
        for field in candidates
        if str(profile.get(field) or "").strip()
    ]
    if not present:
        return None, None

    values = {value for _, value in present}
    if len(values) > 1:
        # Genuinely ambiguous for THIS company. The unsafe list was right.
        return None, None

    return present[0][0], present[0][1]


def is_ambiguous_label(label: str | None) -> bool:
    """Whether this label is one the dictionary refuses as too general."""
    return _normalise(label) in AMBIGUOUS_GROUPS
