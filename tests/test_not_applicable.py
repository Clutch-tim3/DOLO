"""
"N/A" beats a blank — but only when a person has said the field does not apply.

`Comprehensive_Tender_Document_Training_Guide.pdf` opens its four golden rules
with this one, and says internalising the four "will avoid 90% of
disqualification causes":

    1. Complete every field — use "N/A" if not applicable

It is not tidiness. SBD 1's VAT row lists "Leaving blank instead of 'N/A'" as a
named common mistake. SBD 4 is blunter: "If 'not applicable,' write 'N/A' or
'None' — do NOT leave blank." The cross-cutting table gives the same rule for
South African, UN and World Bank submissions alike.

THE DISTINCTION THE WHOLE MODULE EXISTS FOR

    "we are not VAT registered"            -> N/A is the correct answer
    "nobody has told CairoAI our VAT no."  -> N/A is a false statement

The profile cannot tell these apart — both are an empty column — so nothing is
ever inferred from absence. Most of this file is about that line.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_autofill.fill_engine.not_applicable import (
    DECLARABLE,
    NotDeclarable,
    declare,
    declared_for,
    listed,
    withdraw,
)
from agent_autofill.fill_engine.safe_fill_fields import DECLARED_NA_KEY, decide


@pytest.fixture
def company_id():
    cid = f"na-{uuid.uuid4().hex[:10]}"
    yield cid
    for entry in listed(cid):
        withdraw(cid, entry["field"])


def _profile(cid, **extra):
    return {DECLARED_NA_KEY: declared_for(cid), **extra}


# --- absence is not a declaration ------------------------------------------------

def test_an_empty_field_is_still_refused():
    """
    THE line. Nothing was declared, so the field is missing — not absent on
    purpose — and writing N/A would be a claim nobody made.
    """
    verdict = decide("vat_registration_number", "VAT REGISTRATION NUMBER", {})
    assert not verdict.fill
    assert "Nothing on file" in verdict.reason


def test_a_declared_field_fills_with_na(company_id):
    declare(company_id, "vat_registration_number")

    verdict = decide("vat_registration_number", "VAT REGISTRATION NUMBER",
                     _profile(company_id))
    assert verdict.fill
    assert verdict.value == "N/A"
    assert "declared not applicable" in verdict.source


def test_the_declaration_says_who_and_when(company_id):
    """A wrong one has to be findable and undoable."""
    declare(company_id, "fax_number", declared_by="user")
    entry = listed(company_id)[0]

    assert entry["field"] == "fax_number"
    assert entry["declared_by"] == "user"
    assert entry["declared_at"]


def test_a_declaration_can_be_withdrawn(company_id):
    declare(company_id, "fax_number")
    assert withdraw(company_id, "fax_number") is True

    verdict = decide("fax_number", "FAX NUMBER", _profile(company_id))
    assert not verdict.fill, "a withdrawn declaration must stop filling N/A"


def test_declarations_do_not_leak_between_companies(company_id):
    other = f"na-{uuid.uuid4().hex[:10]}"
    declare(company_id, "fax_number")
    try:
        assert declared_for(other) == set()
    finally:
        withdraw(other, "fax_number")


# --- what may never be declared --------------------------------------------------

@pytest.mark.parametrize("field", [
    "company_name", "registration_number", "physical_address",
    "contact_person", "email_address", "bbbee_level",
])
def test_a_field_every_bidder_has_is_refused(field, company_id):
    """
    "N/A" against the bidder's own name is not an honest blank, it is a false
    claim. The refusal names the fields that can be marked instead.
    """
    with pytest.raises(NotDeclarable) as exc:
        declare(company_id, field)
    assert "false statement" in str(exc.value)


def test_only_things_a_company_can_lawfully_lack_are_declarable():
    """
    Each entry is a thing a real South African supplier can genuinely not have:
    VAT registration is only compulsory above the R1m turnover threshold,
    plenty of firms have no fax line or landline, and a supplier may not be on
    the Central Supplier Database yet.

    The list is a floor, not a ceiling — see
    `test_directors_cannot_be_declared_not_applicable` for the entry that was
    removed and why.
    """
    assert DECLARABLE == {
        "vat_registration_number", "fax_number", "csd_number",
        "telephone_number",
    }


def test_nothing_declarable_is_a_yes_no_question():
    """
    The standing rule: "never write 'not applicable' where a yes/no is
    required — it reads as avoiding disclosure." Every declarable field takes a
    value, not an answer.
    """
    for field in DECLARABLE:
        assert not field.startswith("is_")
        assert "state_employee" not in field


# --- the gates upstream are untouched --------------------------------------------

def test_a_declaration_cannot_put_na_on_a_signature(company_id):
    """
    Structural, not a list check. `is_blocked` runs on the LABEL at the top of
    `decide`, so the N/A branch is unreachable for a signature line whatever is
    recorded.
    """
    declare(company_id, "fax_number")
    verdict = decide("fax_number", "SIGNATURE OF BIDDER", _profile(company_id))

    assert not verdict.fill
    assert verdict.value != "N/A"


def test_directors_cannot_be_declared_not_applicable(company_id):
    """
    THIS TEST FOUND A HOLE and the allow-list shrank because of it.

    A sole proprietor has no directors, so "N/A" looked like the same honest
    answer as a missing fax number. But "Name of State institution" maps to
    `director_names_and_id_numbers`, and that cell is in SBD 4's declaration of
    interest — the sworn one. `is_blocked` catches that label only when given
    document context, and `pdf_filler` calls `decide` without it, so the
    declaration wrote "N/A" straight down a sworn table.

    That reads as CairoAI swearing, on the bidder's behalf, that no director is
    employed by the state. The rule has always been that this is asked, never
    inferred and never defaulted.
    """
    with pytest.raises(NotDeclarable):
        declare(company_id, "director_names_and_id_numbers")

    verdict = decide("director_names_and_id_numbers",
                     "Name of State institution", _profile(company_id))
    assert not verdict.fill
    assert verdict.value != "N/A"


def test_a_field_off_the_whitelist_is_still_refused(company_id):
    """The N/A branch sits after the whitelist check, not before it."""
    verdict = decide("something_invented", "SOMETHING", _profile(company_id))
    assert not verdict.fill


def test_a_real_value_always_wins_over_a_declaration(company_id):
    """
    A company that declared no VAT number and later supplied one gets the
    number, not "N/A". The declaration only answers an ABSENCE.
    """
    declare(company_id, "vat_registration_number")
    profile = _profile(company_id, vat_registration_number="4123456789")

    verdict = decide("vat_registration_number", "VAT REGISTRATION NUMBER", profile)
    assert verdict.value == "4123456789"


# --- the profile problems the guide names ----------------------------------------

def test_a_po_box_physical_address_is_caught():
    """
    SBD 1, Physical Address, common mistake: "PO Box only — must be physical".
    CairoAI fills this on every form in a pack, so one bad value repeats
    through the whole submission.
    """
    from agent_autofill.integration.compliance_checks import profile_problems

    problems = profile_problems({"physical_address": "P.O. Box 722, Somerset West"})
    assert len(problems) == 1
    assert problems[0]["field"] == "physical_address"
    assert "not accepted" in problems[0]["message"]


@pytest.mark.parametrize("address", [
    "PO Box 722, Somerset West",
    "P.O. Box 1234",
    "Private Bag X9, Pretoria",
    "postal box 55",
])
def test_the_forms_a_postal_address_takes(address):
    from agent_autofill.integration.compliance_checks import profile_problems

    assert profile_problems({"physical_address": address})


def test_a_real_street_address_is_not_flagged():
    from agent_autofill.integration.compliance_checks import profile_problems

    assert profile_problems(
        {"physical_address": "12 Main Road, Faure, Western Cape, 7131"}) == []


@pytest.mark.parametrize("level", [0, 9, 10])
def test_a_bbbee_level_off_the_scale_is_caught(level):
    """
    "Claimed level MUST match your certificate. Mismatch = disqualification,
    not correction." The profile has held 9, which no certificate can show.
    """
    from agent_autofill.integration.compliance_checks import profile_problems

    problems = profile_problems({"bbbee_level": level})
    assert problems and problems[0]["field"] == "bbbee_level"


@pytest.mark.parametrize("level", [1, 4, 8])
def test_a_valid_level_is_not_flagged(level):
    from agent_autofill.integration.compliance_checks import profile_problems

    assert profile_problems({"bbbee_level": level}) == []


def test_an_absent_level_is_not_flagged():
    """Missing is a question for elsewhere, not a disqualification warning."""
    from agent_autofill.integration.compliance_checks import profile_problems

    assert profile_problems({}) == []
    assert profile_problems({"bbbee_level": None}) == []
    assert profile_problems({"bbbee_level": ""}) == []


# --- the tool --------------------------------------------------------------------

def test_the_tool_is_registered():
    from agent_autofill.integration import autofill_tools

    assert "autofill_mark_not_applicable" in autofill_tools.AUTOFILL_TOOL_HANDLERS
    names = {t["name"] for t in autofill_tools.autofill_tools}
    assert "autofill_mark_not_applicable" in names


def test_the_tool_refuses_an_undeclarable_field(company_id):
    from agent_autofill.integration import autofill_tools

    out = autofill_tools._autofill_mark_not_applicable(company_id, "company_name")
    assert out["status"] == "error"
    assert declared_for(company_id) == set()


def test_the_orchestrator_delivers_declarations_to_the_fill(company_id):
    """
    The join, which the unit tests above all step over by building the profile
    dict themselves.

    `decide` reads the declared set off the profile dict, and the profile dict
    is assembled by the orchestrator. If that hand-off breaks, every test above
    still passes and nothing fills "N/A" on a real pack.
    """
    from pathlib import Path

    from agent_autofill.extraction import extract_document
    from agent_autofill.main_autofill_orchestrator import _with_preference_claim

    fixture = Path(__file__).parent / "fixtures" / "alfred_duma.pdf"
    if not fixture.exists():
        pytest.skip("no PDF fixture available")

    declare(company_id, "vat_registration_number")
    report = extract_document(str(fixture))
    profile, _preference = _with_preference_claim({"company_name": "X"},
                                                  report, company_id)

    assert profile[DECLARED_NA_KEY] == {"vat_registration_number"}
    assert decide("vat_registration_number", "VAT REGISTRATION NUMBER",
                  profile).value == "N/A"


def test_a_missing_company_id_does_not_break_a_fill():
    """
    The orchestrator passes company_id through; an empty one must degrade to
    "nothing declared" rather than raising inside a fill.
    """
    from agent_autofill.fill_engine.not_applicable import declared_for

    assert declared_for("") == set()
    assert declared_for(None) == set()


def test_the_agent_is_told_not_to_infer_it():
    from pathlib import Path
    import agent.main_agent as ma

    prompt = Path(ma.__file__).read_text(encoding="utf-8")
    assert "autofill_mark_not_applicable" in prompt
    assert "NEVER mark a field not applicable because the profile is empty" in prompt
