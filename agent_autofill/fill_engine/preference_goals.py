"""
The SBD 6.1 specific-goals table: read the tender's own goals, claim only what
a document proves, ask about the rest.

`SBD_COMPLIANCE.md` P0-2 asks for "the specific-goals table is filled from the
four `owned_51pc_*` columns". Reading the two real tables the owner has shows
why that cannot be a direct mapping, and the difference is the whole module.

EVERY TENDER DEFINES ITS OWN GOALS

His SBD 6.1 (a scan, hand-completed) lists:

    51% owned by black people / black women / black people with disabilities /
    black youth / black people in rural areas or townships / EME or QSE

Page 53 of his 145-page pack lists something else entirely:

    >51% Youth Ownership          5 / 2.5 / 0
    Local Production and Content  4
    Locality                      3 / 0
    >51% Historically Disadvantaged Individuals   3 / 1.5 / 0
    >51% Women Ownership          3 / 1.5 / 0
    >51% Disability Ownership     2 / 1 / 0

Different goals, different points, and TIERED — ">51%" scores 5 where "10-50%"
scores 2.5. There is no fixed table to fill. The organ of state writes the goals
and the points for each tender, and the tenderer answers those.

WHY MOST ROWS ARE ASKED RATHER THAN CLAIMED

The four stored flags do not mean the same thing as the goals on these forms.

    owned_51pc_black          is not         Historically Disadvantaged
                                             Individuals

HDI is a defined term — persons disenfranchised before the 1994 Constitution,
by race, by gender, or by disability. B-BBEE black ownership overlaps it and is
not it. Claiming three points on that reasoning is precisely what every brief
forbids: "A preference point claimed without qualification is a
misrepresentation to an organ of state."

So a row is claimed automatically ONLY where the stored flag is strictly
NARROWER than the goal, which makes the implication airtight in one direction:

    51% owned by BLACK WOMEN      =>  51% women-owned            yes, always
    51% owned by BLACK YOUTH      =>  51% youth-owned            yes, always
    51% owned by BLACK PEOPLE
      WITH DISABILITIES           =>  51% disability-owned       yes, always

The reverse never holds — a company 51% owned by white women is women-owned and
the flag is False — so a FALSE flag claims nothing and asks nothing either. It
is simply not evidence about the goal.

Everything else is a question with the tender's own numbers in it, which is the
thing the owner has asked for repeatedly:

    "the agent needs to ask me if its not certain ... ill answer all that needs
     to be answered"

NOTHING HERE WRITES ON A FORM

It returns proposals. A proposal becomes a value only through the same confirmed
path as everything else, and the points come from the tender's own allocation
cell — never from a table stored here, because storing one would mean claiming
points a particular tender never offered.
"""

from __future__ import annotations

import re

#: A row's goal text -> the profile flag that PROVES it, where the flag is
#: strictly narrower than the goal so True implies True with no judgement.
#:
#: Deliberately tiny. A goal not listed here is asked about, however obvious the
#: mapping looks: "Historically Disadvantaged Individuals" and "black people"
#: are different legal categories and the difference decides points on a bid.
_PROVEN_BY = (
    (re.compile(r"\bwomen\b", re.I), "owned_51pc_black_women",
     "your B-BBEE certificate records 51%+ black women ownership, which is "
     "also women ownership"),
    (re.compile(r"\byouth\b", re.I), "owned_51pc_black_youth",
     "your B-BBEE certificate records 51%+ black youth ownership, which is "
     "also youth ownership"),
    (re.compile(r"disabilit", re.I), "owned_51pc_black_disability",
     "your B-BBEE certificate records 51%+ ownership by black people with "
     "disabilities, which is also disability ownership"),
)

#: A goal that mentions one of these is about the tender's subject, not about
#: who owns the company, so no ownership flag can speak to it.
_NOT_AN_OWNERSHIP_GOAL = re.compile(
    r"local\s+production|local\s+content|localit|located\s+in|"
    r"sub[- ]?contract|job\s+creation|skills\s+transfer", re.I)

#: The header of the column the tenderer completes.
_CLAIMED_HEADER = re.compile(r"points?\s+claimed", re.I)
_ALLOCATED_HEADER = re.compile(r"points?\s+allocated", re.I)

#: Rows that are not goals. Prefix-matched, not exact: the header row reads
#: "The specific goals allocated points in terms of this tender" and its
#: allocation cell repeats "(80/20 system)", so it carries digits and looked
#: like a goal worth 80 points.
_NOT_A_GOAL = re.compile(
    r"^\s*(total\b|no\.?\s*$|the specific goals|specific goals)", re.I)

#: A cell that is a column heading rather than an allocation.
_IS_HEADING_CELL = re.compile(
    r"points?\s+(allocated|claimed)|to\s+be\s+completed", re.I)


def _cell(row, index):
    value = row[index] if 0 <= index < len(row) else None
    return " ".join((value or "").split())


def find_goals_table(page) -> dict | None:
    """
    The specific-goals table on a pdfplumber page, or None.

    Identified by its own column headers rather than by position: the table is
    on page 53 of one pack and page 3 of another, and its row count changes with
    every tender.
    """
    for table in page.find_tables():
        rows = table.extract()
        flat = " ".join(" ".join(c or "" for c in r) for r in rows)
        if not (_CLAIMED_HEADER.search(flat) and _ALLOCATED_HEADER.search(flat)):
            continue

        claimed_col = allocated_col = None
        for row in rows:
            for index, cell in enumerate(row):
                text = cell or ""
                if claimed_col is None and _CLAIMED_HEADER.search(text):
                    claimed_col = index
                if allocated_col is None and _ALLOCATED_HEADER.search(text):
                    allocated_col = index
            if claimed_col is not None and allocated_col is not None:
                break

        if claimed_col is None or allocated_col is None:
            continue
        return {"table": table, "rows": rows,
                "claimed_col": claimed_col, "allocated_col": allocated_col}
    return None


def goal_rows(found: dict) -> list[dict]:
    """The goal rows of a found table: {goal, allocated, row_index}."""
    out = []
    for index, row in enumerate(found["rows"]):
        goal = _cell(row, 0) or _cell(row, 1)
        allocated = _cell(row, found["allocated_col"])
        if not goal or _NOT_A_GOAL.match(goal) or len(goal) < 4:
            continue
        if _CLAIMED_HEADER.search(goal) or _ALLOCATED_HEADER.search(goal):
            continue
        if not allocated or not any(ch.isdigit() for ch in allocated):
            continue
        if _IS_HEADING_CELL.search(allocated):
            continue
        out.append({"goal": goal, "allocated": allocated, "row_index": index})
    return out


def propose_claims(rows, profile: dict) -> list[dict]:
    """
    What to claim, what to ask, and what to leave — one entry per goal row.

    `action` is one of:

        claim   a stored flag proves it. `points` is the tender's OWN allocated
                figure, copied from the row, and `because` says which document.
        ask     nobody can answer it from what is on file. The question carries
                the tender's numbers so the user can answer it in one line.

    There is no "decline" action. A flag that is False is not evidence the
    company fails the broader goal — 51% white-women ownership is still women
    ownership — so a False flag produces a question, exactly like an absent one.
    Turning it into a silent no would forfeit points the company may be owed.
    """
    proposals = []
    for row in rows:
        goal, allocated = row["goal"], row["allocated"]
        tiers = re.findall(r"\d+(?:\.\d+)?", allocated)

        proven = None
        if not _NOT_AN_OWNERSHIP_GOAL.search(goal):
            for pattern, column, because in _PROVEN_BY:
                if pattern.search(goal) and bool((profile or {}).get(column)):
                    proven = (column, because)
                    break

        if proven and tiers:
            column, because = proven
            proposals.append({
                "goal": goal, "action": "claim",
                # The highest tier on the row. ">51%" is the top band and the
                # flag means 51% or more; a lower tier would under-claim a point
                # the certificate supports.
                "points": tiers[0],
                "allocated": allocated,
                "from_column": column,
                "because": because,
                "row_index": row["row_index"],
            })
            continue

        proposals.append({
            "goal": goal, "action": "ask",
            "points": None,
            "allocated": allocated,
            "from_column": None,
            "question": (
                f"This tender allocates {allocated} for “{goal}”. "
                f"Do you qualify, and for how many points?"
            ),
            "row_index": row["row_index"],
        })
    return proposals
