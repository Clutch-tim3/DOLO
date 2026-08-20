"""
The agent asks for what it needs, and remembers what it is told.

P0-2. The owner: "anything personal like name or ID number should be asked by
agent, agent should ask for clarification wherever it isn't sure of things."

A field with no data became a line in a flag list the user had to find, read
and acknowledge. For a director's ID number — a value the user has and the
system simply does not — that is the wrong interaction. On his pack, 24
outstanding fields collapse to ELEVEN questions, because "Designation" and
"Capacity" appear fourteen times between them and are one fact.

P1-5. "sometimes it even doesn't understand the field so I need this to learn
through every tender." 205 labels on that pack are real questions the
dictionary does not know.

THE CONSTRAINT THAT MATTERS MOST

Nothing learned may cause a signature, a price or a sworn declaration to be
filled. That is not enforced by a rule inside the learning module — it is
enforced by where the module sits: it answers only when the dictionary cannot,
and its answer goes through `is_blocked` and `SAFE_FILL_FIELDS` exactly as a
dictionary match does. Most of the tests below are about that boundary.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_autofill.extraction import learned_labels as learn
from agent_autofill.integration.missing_fields import (
    FIELD_PROMPTS,
    PERSONAL_FIELDS,
    SWORN_SUBFIELDS,
    missing_profile_fields,
)

NO_DATA = "Nothing on file for this field yet — add it in your company profile."
BLOCKED = "Requires your signature. Agent Autofill never signs anything on your behalf."


class _Match:
    def __init__(self, canonical, confident):
        self.canonical = canonical
        self.is_confident = confident


@pytest.fixture
def company_id():
    cid = f"learn-{uuid.uuid4().hex[:10]}"
    yield cid
    for lesson in learn.lessons(cid):
        learn.forget(cid, lesson["example_label"] or lesson["normalised"])


# --- P0-2: asking ------------------------------------------------------------

def test_one_question_per_fact_not_per_blank():
    """
    "Designation" x7 and "Capacity" x6 are both authorized_signatory_capacity.
    Asking thirteen times for one fact is the same failure as flagging it
    thirteen times.
    """
    rows = ([{"label": "Designation", "location": f"page {i}", "reason": NO_DATA}
             for i in range(7)]
            + [{"label": "Capacity", "location": f"page {i}", "reason": NO_DATA}
               for i in range(6)])

    questions = missing_profile_fields(rows, {})
    capacity = [q for q in questions if q["field"] == "authorized_signatory_capacity"]

    assert len(capacity) == 1, "asked more than once for the same fact"
    assert capacity[0]["count"] == 13
    assert set(capacity[0]["asked_by"]) <= {"Designation", "Capacity"}


def test_only_what_the_user_can_actually_answer_is_asked():
    """
    A blocked signature is not something the user can supply by typing. Asking
    would be noise, and it is the flag list's job.
    """
    rows = [{"label": "Signature", "location": "page 9", "reason": BLOCKED},
            {"label": "CELL PHONE NUMBER", "location": "page 7", "reason": NO_DATA}]

    fields = {q["field"] for q in missing_profile_fields(rows, {})}
    assert fields == {"standard_cell"}


def test_a_field_already_answered_is_never_asked_again():
    """
    "Values asked for once are never asked for again" — because once written
    they fill, so they stop appearing as missing.
    """
    rows = [{"label": "CELL PHONE NUMBER", "location": "page 7", "reason": NO_DATA}]

    assert missing_profile_fields(rows, {}) != []
    assert missing_profile_fields(rows, {"standard_cell": "082 555 0134"}) == []


def test_a_placeholder_does_not_count_as_answered():
    """"Pending" in the profile is the absence of a value, so it is still asked."""
    rows = [{"label": "VAT REGISTRATION NUMBER", "location": "page 7", "reason": NO_DATA}]
    assert missing_profile_fields(rows, {"vat_registration_number": "Pending"}) != []


def test_every_question_has_words_to_ask_with():
    """A field name is not a question. The agent needs something to say."""
    rows = [{"label": lbl, "location": "page 1", "reason": NO_DATA}
            for lbl in ("CSD NUMBER", "E-MAIL ADDRESS", "TAX COMPLIANCE SYSTEM PIN")]
    for q in missing_profile_fields(rows, {}):
        assert q["prompt"], f"{q['field']} has no prompt"
        assert len(q["prompt"]) > 10


def test_personal_details_are_marked_as_such():
    """So the agent asks for them with the care they deserve."""
    rows = [{"label": "CELL PHONE NUMBER", "location": "page 7", "reason": NO_DATA}]
    assert missing_profile_fields(rows, {})[0]["personal"] is True

    rows = [{"label": "VAT REGISTRATION NUMBER", "location": "page 7", "reason": NO_DATA}]
    assert missing_profile_fields(rows, {})[0]["personal"] is False


def test_the_most_needed_fact_is_asked_first():
    rows = ([{"label": "Designation", "location": "p1", "reason": NO_DATA}] * 7
            + [{"label": "CSD NUMBER", "location": "p1", "reason": NO_DATA}])
    assert missing_profile_fields(rows, {})[0]["field"] == "authorized_signatory_capacity"


def test_the_sworn_declaration_is_carried_with_the_directors_question():
    """
    is_state_employee is a sworn SBD 4 declaration. It is asked, never
    inferred, never defaulted — and never carried over from another tender.
    """
    assert "is_state_employee" in SWORN_SUBFIELDS
    text = SWORN_SUBFIELDS["is_state_employee"].lower()
    assert "sworn" in text and "never assumes" in text


def test_the_prompt_table_covers_the_fillable_fields():
    """A field with no prompt is one the agent cannot ask for."""
    from agent_autofill.fill_engine.safe_fill_fields import SAFE_FILL_FIELDS

    columns = set(SAFE_FILL_FIELDS.values())
    missing = sorted(columns - set(FIELD_PROMPTS))
    assert not missing, f"no wording to ask for: {missing}"


# --- P0-2: the write and the refill ------------------------------------------

def test_the_agent_is_told_to_show_the_value_before_saving():
    """
    confirmed=True asserts a person saw THAT value. The prompt has to say so,
    because the model is what decides when to set it.
    """
    from agent.main_agent import build_system_prompt

    prompt = build_system_prompt("acme").lower()
    assert "autofill_missing_details" in prompt
    assert "confirmed=true" in prompt
    assert "sworn declaration" in prompt
    assert "never infer" in prompt or "never assumes" in prompt or "never infer it" in prompt


def test_refill_returns_a_new_review_rather_than_editing_the_old_one():
    """
    Re-filling changes what is written. An acknowledgement is a statement that
    a person saw a SPECIFIC value, so carrying them across would record that
    someone reviewed something they never saw.
    """
    import inspect

    from agent_autofill.integration.review_gate import refill_review

    source = inspect.getsource(refill_review)
    assert "open_review(" in source, "refill does not open a fresh review"
    assert "export_path" in source, "refill does not refuse an exported document"


def test_both_new_tools_are_advertised_and_handled():
    """
    P0-3 taught this the hard way: a handler the schema does not mention is a
    capability the model cannot reach.
    """
    import agent_autofill.integration.autofill_tools as tools

    schemas = {t["name"] for t in tools.autofill_tools}
    handlers = set(tools.AUTOFILL_TOOL_HANDLERS)

    assert "autofill_missing_details" in schemas and "autofill_missing_details" in handlers
    assert "autofill_refill" in schemas and "autofill_refill" in handlers
    assert schemas == handlers


# --- P1-5: learning ----------------------------------------------------------

def test_the_same_question_asked_differently_is_one_lesson(company_id):
    learn.teach(company_id, "E-MAIL ADDRESS:", canonical_field="email_address")
    for variant in ("e-mail address", "E-mail Address", "  EMAIL   ADDRESS  "):
        assert learn.lookup(company_id, variant) is not None


def test_numbered_rows_stay_separate(company_id):
    """"Director 1" and "Director 2" are different rows of a table."""
    assert learn.normalise("Director 1") != learn.normalise("Director 2")


def test_a_lesson_is_used_when_the_dictionary_has_no_answer(company_id):
    learn.teach(company_id, "Name of State institution", canonical_field="company_name")

    field, source = learn.apply_learning(company_id, "Name of State institution",
                                         _Match(None, False))
    assert field == "company_name"
    assert "previous tender" in source


def test_a_confident_dictionary_match_always_wins(company_id):
    """
    Lessons fill gaps, they do not override the shared vocabulary — otherwise a
    company could teach itself that "Signature" means "company_name".
    """
    learn.teach(company_id, "Signature", canonical_field="company_name")

    field, source = learn.apply_learning(company_id, "Signature",
                                         _Match("signature", True))
    assert field == "signature"
    assert source == "dictionary"


def test_a_label_can_be_taught_to_be_nothing(company_id):
    """"This is not a field" is as useful a lesson as "this means X"."""
    learn.teach(company_id, "POINTS", not_a_field=True)
    field, source = learn.apply_learning(company_id, "POINTS", None)
    assert field is None
    assert "previous tender" in source


def test_the_user_is_told_where_a_decision_came_from(company_id):
    """A system that learns invisibly is one nobody can argue with."""
    learn.teach(company_id, "Contract description", canonical_field="company_name")
    assert learn.lookup(company_id, "Contract description")["source"] == (
        "learned from a previous tender")


def test_a_wrong_lesson_can_be_corrected(company_id):
    learn.teach(company_id, "Reference", canonical_field="company_name")
    assert learn.forget(company_id, "Reference") is True
    assert learn.lookup(company_id, "Reference") is None
    assert learn.forget(company_id, "Reference") is False


def test_one_company_never_teaches_another(company_id):
    """
    A lesson from one customer's tender must not change how another customer's
    form is read. Same tenancy rule as everywhere else.
    """
    other = f"other-{uuid.uuid4().hex[:8]}"
    learn.teach(company_id, "Reference number", canonical_field="registration_number")

    assert learn.lookup(other, "Reference number") is None
    field, _ = learn.apply_learning(other, "Reference number", _Match(None, False))
    assert field is None


def test_nothing_is_learned_by_observation():
    """
    A fill that was not corrected is not evidence it was right — the user may
    simply not have looked. `teach` is the only way in, and it needs an
    explicit answer.
    """
    import inspect

    source = inspect.getsource(learn)
    assert "def teach(" in source
    with pytest.raises(ValueError):
        learn.teach("co", "Some label")  # neither a field nor not_a_field


def test_learning_cannot_reach_the_blocklist():
    """
    THE constraint. apply_learning returns a canonical field and nothing more —
    the fill-or-refuse decision is made downstream by is_blocked and
    SAFE_FILL_FIELDS, which this module does not import and cannot influence.
    """
    import ast
    import inspect

    # Parsed, not grepped: the module docstring names all three deliberately,
    # to explain why it must not touch them.
    tree = ast.parse(inspect.getsource(learn))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            imported.add(node.id)
        elif isinstance(node, ast.Attribute):
            imported.add(node.attr)

    for forbidden in ("never_fill_fields", "is_blocked", "SAFE_FILL_FIELDS",
                      "decide", "fill_pdf", "fill_docx"):
        assert forbidden not in imported, (
            f"learning reaches {forbidden}; the fill-or-refuse decision must stay "
            f"downstream where a lesson cannot influence it"
        )
