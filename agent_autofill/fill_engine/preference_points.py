"""
The SBD 6.1 preference points claim: arithmetic, from documents, or nothing.

`SBD_COMPLIANCE.md`, P0-2:

    "Donington Vale is Level 1, so under 80/20 the claim is 20 points. That is
     arithmetic from a documented fact, and leaving it blank costs points for
     no reason."

Two inputs, both facts rather than judgements:

  * the B-BBEE level, read off the certificate and stored on the profile;
  * the preference system, STATED ON THE FORM by the organ of state.

The mapping between them is fixed by regulation, and `models.sa_scoring.
get_bbbee_points` already holds it. Nothing here re-implements that table.

WHY THE SYSTEM IS READ AND NOT INFERRED

`sa_scoring.get_evaluation_system` derives the system from the tender's value
against the R50m PPPFA threshold. That is a rule about which system an organ of
state SHOULD choose. It is not what this tender says, and the difference is not
academic: a Level 1 bidder claims 20 points under 80/20 and 10 under 90/10, so
guessing wrong either doubles or halves the claim on a live bid.

The form states it. Reading it is a fact; deriving it is a guess.

WHY BOTH SYSTEMS APPEAR IN EVERY PACK

Measured on all three of the owner's real documents: every one contains both
"80/20" and "90/10". SBD 6.1's own boilerplate explains the choice —

    "in cases where organs of state intend to use Regulation 3(2)... that
     either the 80/20 or 90/10 preference point system will apply"

— so counting mentions, or matching the first one, produces a confident wrong
answer. Only a DECLARATIVE statement counts: the form saying which one applies
to this tender, or its goals table naming one in its own heading.

WHEN IT CANNOT TELL, IT ASKS

Ambiguous or absent, the answer is None, and the caller turns that into a
question. That is the whole point:

    "the agent needs to ask me if its not certain ... ill answer all that needs
     to be answered"

A blank claim costs points. A wrong claim is a misrepresentation to an organ of
state. A question costs one line of conversation, so an unresolved system is
never resolved by picking one.
"""

from __future__ import annotations

import re

from models.sa_scoring import SYSTEM_80_20, SYSTEM_90_10, get_bbbee_points

#: The form saying which system applies to THIS tender. Both orders occur:
#: "the 80/20 preference point system will be applicable" and "the preferential
#: Procurement System Applicable is 80/20" — the second is from the owner's
#: iThemba LABS RFQ, page 1.
_DECLARATIONS = (
    re.compile(r"(80\s*/\s*20|90\s*/\s*10)[^.\n]{0,60}?"
               r"\b(?:is|shall\s+be|will\s+be|are)\b[^.\n]{0,20}?applicab", re.I),
    # "The applicable preference point system for this tender is the 80/20
    # preference point system" — the standard wording, and 47 characters from
    # "applicable" to the number, so the window has to be generous.
    re.compile(r"applicab\w*[^.\n]{0,60}?(80\s*/\s*20|90\s*/\s*10)", re.I),
    # "QUOTATIONS WILL BE EVALUATED ON THE 80/20 POINT SCORING SYSTEM"
    re.compile(r"evaluat\w*[^.\n]{0,40}?(80\s*/\s*20|90\s*/\s*10)", re.I),
    re.compile(r"\bpoints?\s+allocated\b[^.\n]{0,30}?"
               r"\((80\s*/\s*20|90\s*/\s*10)\s*system\)", re.I),
)

#: Sentences that MENTION a system while explaining the choice rather than
#: making it. A match inside one of these is evidence of nothing.
_BOILERPLATE = re.compile(
    r"(either\s+the|whether\s+the|unclear\s+whether|in\s+cases\s+where|"
    r"note\s+to\s+organs?\s+of\s+state|corresponding\s+points)", re.I)

AMBIGUOUS = "ambiguous"
ABSENT = "absent"


def _normalise(raw: str) -> str:
    return SYSTEM_80_20 if "8" in raw else SYSTEM_90_10


def detect_preference_system(text: str) -> tuple[str | None, str]:
    """
    Which preference system this tender states. (system, evidence).

    `evidence` is the sentence it was read from, so the user can check it — or
    AMBIGUOUS / ABSENT when there is no answer. Both of those mean ask.
    """
    body = text or ""
    found: dict[str, str] = {}

    for pattern in _DECLARATIONS:
        for match in pattern.finditer(body):
            start = body.rfind("\n", 0, match.start()) + 1
            end = body.find("\n", match.end())
            sentence = body[start:end if end != -1 else len(body)].strip()

            # "either the 80/20 or 90/10 ... will apply" is the form explaining
            # the choice, not making it.
            if _BOILERPLATE.search(sentence):
                continue

            system = _normalise(match.group(1))
            found.setdefault(system, " ".join(sentence.split())[:200])

    if len(found) == 1:
        system, evidence = next(iter(found.items()))
        return system, evidence
    if len(found) > 1:
        return None, AMBIGUOUS
    return None, ABSENT


def points_claim(bbbee_level, system: str | None):
    """
    Points to claim for a level under a system, or None.

    None whenever either input is missing. The claim is written only when both
    are known, because the alternative to a blank is a number on a government
    form that nothing supports.
    """
    if system not in (SYSTEM_80_20, SYSTEM_90_10):
        return None
    try:
        level = int(bbbee_level)
    except (TypeError, ValueError):
        return None
    if not 1 <= level <= 8:
        # 0 and 9 are the sentinels the profile uses for "not set". The scale is
        # 1 to 8; a claim from anything else would be invented.
        return None
    return get_bbbee_points(level, system)


#: The four SBD 6.1 specific goals CairoAI holds a documented answer for, in the
#: order the form lists them, mapped to the profile column that proves each.
#:
#: A fifth and sixth goal — 51% owned by black people living in rural areas or
#: townships, and EME/QSE status — appear on the owner's own SBD 6.1 and there
#: is NO column for either. They are not guessed and not left implied: they are
#: reported as unanswerable so the user is asked. See `goal_claims`.
GOAL_COLUMNS = (
    ("51% owned by black people", "owned_51pc_black"),
    ("51% owned by black people who are women", "owned_51pc_black_women"),
    ("51% owned by black people with disabilities", "owned_51pc_black_disability"),
    ("51% owned by black people who are youth", "owned_51pc_black_youth"),
)


def goal_claims(profile: dict) -> list[dict]:
    """
    Which specific goals this company can claim, from the four stored flags.

    THE RULE, from SBD_COMPLIANCE.md and every brief before it: "Never claim a
    goal the certificate does not support — that is a misrepresentation, and the
    four flags exist precisely so it comes from a document rather than an
    assumption."

    So there are three states, not two:

        True   claim it — the certificate says 51% or more
        False  leave the row BLANK — the certificate says otherwise, which is
               what the owner's own hand-completed SBD 6.1 does for the women
               and disability rows, both 0% on his certificate
        None   nothing on file. Not a no. Ask.

    Returns the states only. The POINTS for a claimed goal are printed on the
    form by the organ of state, per tender, and are copied from that row — they
    are not stored here and must never be assumed.
    """
    out = []
    for goal, column in GOAL_COLUMNS:
        raw = (profile or {}).get(column)
        out.append({
            "goal": goal,
            "column": column,
            "qualifies": None if raw is None else bool(raw),
            "source": "B-BBEE certificate" if raw is not None else None,
        })
    return out
