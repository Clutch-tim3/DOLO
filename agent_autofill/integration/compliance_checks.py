"""
The checks a procurement officer runs before reading a word of the proposal.

`SBD_COMPLIANCE.md`:

    "Administrative mistakes disqualify more South African tender submissions
     than weak pricing or poor technical proposals... CairoAI fills these forms.
     That puts it in a position to catch every one of those failures before
     submission, and it currently catches none of them."

Three checks, all of which need something only CairoAI has — the whole pack at
once, alongside the profile and the vault:

  P0-1  the same fact, the same on every form
  P0-3  no certificate expired at closing
  P0-4  what would get this bid thrown out, said before what was filled

NONE OF THIS FILLS ANYTHING. It reads a finished draft and reports. A check that
started writing values would be deciding, and the decisions belong upstream in
`safe_fill_fields` where they can be audited in one place.
"""

from __future__ import annotations

import re
from datetime import date, datetime

# --------------------------------------------------------------------------
# P0-3 · Validity at closing
# --------------------------------------------------------------------------

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

#: "18 August 2026", "22-March-2027", "18 Aug 2026".
_DMY_WORD = re.compile(
    r"\b(\d{1,2})\s*[-/ ]\s*([A-Za-z]{3,9})\s*[-/ ]\s*(\d{4})\b")
#: "August 18, 2026"
_MDY_WORD = re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b")
#: "2026/08/18" and "2026-08-18"
_ISO = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
#: "18/08/2026". Day-first: SA convention, and the reason this is separate.
_DMY_NUM = re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b")


def parse_date(text: str) -> date | None:
    """A South African tender date, or None. Day-first where it is ambiguous."""
    if not text:
        return None
    body = text.strip()

    match = _DMY_WORD.search(body)
    if match:
        month = _MONTHS.get(match.group(2).lower())
        if month:
            return _safe(int(match.group(3)), month, int(match.group(1)))

    match = _MDY_WORD.search(body)
    if match:
        month = _MONTHS.get(match.group(1).lower())
        if month:
            return _safe(int(match.group(3)), month, int(match.group(2)))

    match = _ISO.search(body)
    if match:
        return _safe(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    match = _DMY_NUM.search(body)
    if match:
        # 08/09/2026 is 8 September here, not 9 August. Getting this backwards
        # would report a certificate as valid a month after it expired.
        return _safe(int(match.group(3)), int(match.group(2)), int(match.group(1)))

    return None


def _safe(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


#: Wording that introduces the moment a tender shuts. Ordered: the more specific
#: the phrase, the more it is worth trusting.
_CLOSING_PATTERNS = (
    re.compile(r"closing\s+date[^\n:]*[:\s]+([^\n]{4,60})", re.I),
    re.compile(r"not\s+later\s+than[^\n]{0,20}?on\s+([^\n]{4,40})", re.I),
    re.compile(r"clos\w+[^\n]{0,30}?\bon\s+([^\n]{4,40})", re.I),
    re.compile(r"(?:tenders?|quotations?|bids?)\s+close[^\n]{0,30}?([^\n]{4,40})", re.I),
)


def find_closing_date(text: str) -> date | None:
    """
    When this tender closes, or None.

    None is a real answer and must stay one. A guessed closing date would
    silently clear an expired certificate, which is the failure this check
    exists to catch.
    """
    body = text or ""
    for pattern in _CLOSING_PATTERNS:
        for match in pattern.finditer(body):
            found = parse_date(match.group(1))
            if found:
                return found
    return None


#: Inside this many days of closing, a document that is still valid is worth
#: mentioning. The brief: "'Expires in 3 weeks, this tender closes in 5' is
#: worth saying too."
EXPIRY_WARNING_DAYS = 30


def expiry_problems(closing: date | None, documents) -> list[dict]:
    """
    Documents that will not be valid when this tender closes.

    `documents` are company_documents rows carrying `expiry_date`. A B-BBEE
    certificate must be valid AT CLOSING — an expired one scores zero, and a
    preference claim without valid proof scores zero with it.

    With no closing date, this reports nothing rather than comparing against
    today. Today is not the deadline, and answering a question nobody asked with
    the wrong date is worse than saying it could not be checked.
    """
    if closing is None:
        return []

    problems = []
    for row in documents or []:
        raw = _get(row, "expiry_date")
        expiry = parse_date(str(raw)) if raw else None
        if expiry is None:
            continue

        kind = _get(row, "document_type") or "document"
        days = (expiry - closing).days
        if days < 0:
            problems.append({
                "document_type": kind,
                "expiry": expiry.isoformat(),
                "closing": closing.isoformat(),
                "severity": "expired",
                "message": (
                    f"Your {kind} expires {expiry.isoformat()}, before this "
                    f"tender closes on {closing.isoformat()}. An expired "
                    f"certificate scores zero, and a preference claim without "
                    f"valid proof scores zero with it."
                ),
            })
        elif days <= EXPIRY_WARNING_DAYS:
            problems.append({
                "document_type": kind,
                "expiry": expiry.isoformat(),
                "closing": closing.isoformat(),
                "severity": "expiring",
                "message": (
                    f"Your {kind} expires {expiry.isoformat()}, only {days} "
                    f"day(s) after this tender closes on {closing.isoformat()}."
                ),
            })
    return problems


#: A refusal that is a signature line. These are the tasks the owner must do by
#: hand before submitting, and one missing signature disqualifies a submission.
_SIGNATURE_REASON = re.compile(r"signature|sign(ed|ing)?\b", re.I)


def signature_tasks(skipped) -> list[dict]:
    """
    Every signature line left blank, with its page.

    `SBD_COMPLIANCE.md` P0-4 asks for these to be counted off the `[ ! ]` marks
    on the page. There are no marks any more — the owner had them removed
    because they survive printing and he was handing organs of state statutory
    forms covered in them. The refusal RECORD was always the real source, so
    this counts from that instead, which is also the only source that works for
    a form CairoAI never drew on.
    """
    out = []
    for item in skipped or []:
        reason = str(getattr(item, "reason", "") or "")
        label = str(getattr(item, "label", "") or "")
        if _SIGNATURE_REASON.search(reason) or _SIGNATURE_REASON.search(label):
            out.append({
                "label": label or "(signature)",
                "location": getattr(item, "location", "") or "",
            })
    return out


def disqualification_summary(fill_result, profile: dict, *,
                             closing=None, documents=None,
                             goal_proposals=None) -> dict:
    """
    What would get this bid thrown out, ahead of what was filled.

    P0-4: "the export summary leads with what would disqualify this bid, ahead
    of what was filled. A user who reads nothing else should still see the four
    signatures they have to add."

    Nothing here blocks an export on its own. The export gate is
    `review_gate.export_reviewed` and stays the only thing that refuses; this
    tells a person what to look at, which is a different job. Making it a second
    gate would put two things in a position to refuse an export and guarantee
    they disagree eventually.
    """
    filled = list(getattr(fill_result, "filled", None) or [])
    skipped = list(getattr(fill_result, "skipped", None) or [])

    signatures = signature_tasks(skipped)
    conflicts = cross_form_conflicts(filled)
    expiries = expiry_problems(closing, documents)

    blocking = []
    if signatures:
        # Sorted by page NUMBER, not as strings: "page 7" belongs before
        # "page 117", and this list is read by a person working through a pack.
        pages = ", ".join(sorted(
            {s["location"] for s in signatures if s["location"]},
            key=lambda loc: (int(re.search(r"\d+", loc).group())
                             if re.search(r"\d+", loc) else 0, loc)))
        blocking.append(
            f"{len(signatures)} signature line(s) need signing by hand"
            + (f" — {pages}." if pages else ".")
        )

    directors = (profile or {}).get("directors") or []
    if signatures and len(directors) > 1:
        blocking.append(
            f"This company has {len(directors)} directors on file. Where a form "
            f"requires all directors or members to sign, one missing signature "
            f"disqualifies the whole submission — check which of these need all "
            f"of them."
        )

    for conflict in conflicts:
        blocking.append(
            f"'{conflict['field']}' is written two different ways in this pack: "
            + " vs ".join(f"“{v['value']}” ({v['location']})"
                          for v in conflict["values"][:2])
        )

    for problem in expiries:
        blocking.append(problem["message"])

    claimed = [p for p in (goal_proposals or []) if p.get("action") == "claim"]
    if claimed:
        blocking.append(
            f"{len(claimed)} preference goal(s) are claimed on your B-BBEE "
            f"certificate. Attach it — a claim without proof scores zero."
        )

    return {
        "would_disqualify": blocking,
        "signature_tasks": signatures,
        "cross_form_conflicts": conflicts,
        "expiry_problems": expiries,
        "closing_date": closing.isoformat() if closing else None,
        "filled_count": len(filled),
        "message": (
            "Before you submit: " + " ".join(blocking)
            if blocking else
            "No administrative problems found in this draft."
        ),
    }


def _get(row, key):
    """A sqlite3.Row, a dict or an object — the callers differ."""
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return getattr(row, key, None)


# --------------------------------------------------------------------------
# P0-1 · The same fact on every form
# --------------------------------------------------------------------------

#: Facts that appear on more than one SBD and must agree. A registration number
#: that differs between SBD 1 and SBD 4 reads as carelessness or
#: misrepresentation, and both mean rejection.
CROSS_FORM_FIELDS = (
    "company_name", "registration_number", "csd_number",
    "tax_reference_number", "vat_registration_number", "bbbee_level",
    "director_names_and_id_numbers",
)


def _comparable(value: str) -> str:
    """
    What counts as the same answer.

    Case, spacing and punctuation vary between forms for what is plainly one
    value — "2016/123456/07" and "2016 123456 07" — and reporting those as a
    mismatch would bury the real ones.
    """
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def cross_form_conflicts(filled) -> list[dict]:
    """
    The same field answered differently in different places in one pack.

    CairoAI fills the whole pack, so it is the only party that sees every form
    at once. "A human checking this manually across six documents is exactly
    where a tired person makes a mistake at 11pm."

    Compares by LABEL rather than by canonical field, because a `FilledField`
    carries the form's label and its page. That is deliberate: two labels that
    mean the same thing and disagree is the case worth catching, and it is found
    by grouping on the value's own field name where available and falling back
    to the label.
    """
    seen: dict[str, list] = {}
    for item in filled or []:
        field = getattr(item, "canonical_field", None) or getattr(item, "label", "")
        key = _comparable(str(field))
        if key:
            seen.setdefault(key, []).append(item)

    conflicts = []
    for group in seen.values():
        values = {}
        for item in group:
            values.setdefault(_comparable(str(item.value)), item)
        if len(values) > 1:
            conflicts.append({
                "field": getattr(group[0], "canonical_field", None)
                         or getattr(group[0], "label", ""),
                "values": [
                    {"value": str(i.value), "label": i.label, "location": i.location}
                    for i in values.values()
                ],
                "message": (
                    "The same detail is written differently in two places in "
                    "this pack. Procurement reads a mismatch between forms as "
                    "carelessness or misrepresentation, and both mean rejection."
                ),
            })
    return conflicts
