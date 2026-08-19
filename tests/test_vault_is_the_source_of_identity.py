"""
A form is filled with the details of the company whose form it is.

The owner ran a real tender pack through Agent Autofill and it filled the form
with another company's name, registration number and addresses.

The Compliance Vault and the autofill profile were two tables with nothing
between them:

    company_archive   (what the Vault writes)   DONINGTON VALE, the real company
    company_profile   (what the fill engine reads)  CairoAI (Pty) Ltd, left over

The user uploaded Donington Vale's CIPC documents; the fill engine read the
other table and wrote what it found. From the outside that looked like invented
details. It was worse: a wrong value with complete provenance.

The specific wrong values came from placeholder data written into
enterprise_corp during earlier testing. That made the failure louder. It is not
the bug. The bug is two stores of company identity and a fill engine reading
the one the user never edits.

There are two defences here and they are independent on purpose. The Vault
writes through, so the tables cannot disagree; and `resolve_value` refuses
placeholders, so nothing that did get in reaches a form.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent.memory import company_store
from agent_autofill.fill_engine import safe_fill_fields
from agent_autofill.fill_engine.safe_fill_fields import decide, resolve_value

# Resolved defensively rather than imported by name: importing a new symbol
# directly makes this whole module fail on collection against the version that
# had the bug, which looks like a caught regression and is not one. The
# fallback says a value is never a placeholder, so the assertions below fail on
# the behaviour instead.
is_sentinel = getattr(safe_fill_fields, "is_sentinel", lambda value: value in (None, ""))


@pytest.fixture
def company_id():
    cid = f"vault-{uuid.uuid4().hex[:10]}"
    yield cid
    company_store.delete_company_profile(cid)


# --- placeholders never reach a form ------------------------------------------

@pytest.mark.parametrize("placeholder", [
    "Pending", "pending", "  PENDING  ", "N/A", "n/a", "NA", "TBC", "tba",
    "Unknown", "None", "not applicable", "Not Available", "nil", "-", "--",
    "null", "to be confirmed", "", "   ",
])
def test_a_placeholder_is_treated_as_absent(placeholder):
    """
    "Pending" is what the Vault stores when it cannot extract a registration
    number, and it reached a bid form AS the registration number. A placeholder
    is the absence of a value wearing a label, and on a submitted form it is
    worse than a blank — a blank is visibly unfinished.
    """
    assert is_sentinel(placeholder)

    value, source = resolve_value("registration_number",
                                  {"registration_number": placeholder})
    assert value is None, f"{placeholder!r} would be written onto a form"
    assert source is None


def test_a_real_registration_number_still_fills():
    """Refusing placeholders must not refuse real values."""
    value, source = resolve_value("registration_number",
                                  {"registration_number": "2020/123456/07"})
    assert value == "2020/123456/07"
    assert source


def test_the_refused_field_is_reported_as_left_for_a_person(company_id):
    """
    A refusal has to be visible. Silently skipping it looks identical to the
    field not existing, and the person never learns they must complete it.
    """
    decision = decide("registration_number", "Registration Number",
                      {"registration_number": "Pending"})
    assert decision.fill is False
    assert decision.reason and "company profile" in decision.reason.lower()


@pytest.mark.parametrize("level", [9, "9", 0, "0"])
def test_a_sentinel_bbbee_level_is_not_written(level):
    """
    9 is this codebase's stand-in for "non-compliant or unknown" — the scale is
    1-8 and get_bbbee_points returns 0.0 outside it. app.py writes 9 when the
    Vault extracts nothing, so it conflates "could not read it" with "they are
    non-compliant". Neither belongs on a form as a level.
    """
    value, _ = resolve_value("bbbee_level", {"bbbee_level": level})
    assert value is None


@pytest.mark.parametrize("level", [1, 4, 8])
def test_a_real_bbbee_level_still_fills(level):
    value, _ = resolve_value("bbbee_level", {"bbbee_level": level})
    assert value is not None


# --- the Vault writes through -------------------------------------------------

def test_an_uploaded_document_populates_the_profile(company_id):
    """
    The user's expectation, and the correct one: everything is in the Vault, so
    it should never fill incorrect details.
    """
    from app import sync_vault_to_profile

    parsed = {
        "company_name": "DONINGTON VALE",
        "registration_number": "2020/654321/07",
        "supplier_number": "MAAA1234567",
        "bbbee_level": 2,
    }
    result = sync_vault_to_profile(company_id, parsed)

    assert set(result["updated"]) == {"company_name", "registration_number",
                                      "csd_number", "bbbee_level"}

    profile = company_store.get_company_profile(company_id)
    assert profile["company_name"] == "DONINGTON VALE"
    assert profile["registration_number"] == "2020/654321/07"
    assert profile["csd_number"] == "MAAA1234567"


def test_the_form_then_fills_with_that_company(company_id):
    """End to end: what the Vault established is what the fill engine writes."""
    from app import sync_vault_to_profile

    sync_vault_to_profile(company_id, {
        "company_name": "DONINGTON VALE",
        "registration_number": "2020/654321/07",
    })
    profile = company_store.get_company_profile(company_id)

    assert resolve_value("company_name", profile)[0] == "DONINGTON VALE"
    assert resolve_value("registration_number", profile)[0] == "2020/654321/07"


def test_a_document_that_extracted_nothing_does_not_overwrite_real_values(company_id):
    """
    The failure running the other way. If "Pending" wrote through, a second
    upload that parsed badly would replace a good registration number with a
    placeholder.
    """
    from app import sync_vault_to_profile

    sync_vault_to_profile(company_id, {"company_name": "DONINGTON VALE",
                                       "registration_number": "2020/654321/07"})

    result = sync_vault_to_profile(company_id, {
        "company_name": "DONINGTON VALE",
        "registration_number": "Pending",
        "supplier_number": "Pending",
        "bbbee_level": 9,
    })

    assert "registration_number" not in result["updated"]
    profile = company_store.get_company_profile(company_id)
    assert profile["registration_number"] == "2020/654321/07"


def test_nothing_is_written_when_a_document_yields_no_facts(company_id):
    from app import sync_vault_to_profile

    result = sync_vault_to_profile(company_id, {"registration_number": "Pending",
                                                "bbbee_level": 9})
    assert result["updated"] == []
    assert company_store.get_company_profile(company_id) == {}


# --- the tenancy guarantee the brief asks for ---------------------------------

def test_a_form_is_never_filled_from_another_companys_profile():
    """
    The test the brief names: a form filled for a company must show that
    company's details. Two companies, two profiles, and the fill engine reading
    one must never surface the other's values.
    """
    from app import sync_vault_to_profile

    a = f"vault-a-{uuid.uuid4().hex[:8]}"
    b = f"vault-b-{uuid.uuid4().hex[:8]}"
    try:
        sync_vault_to_profile(a, {"company_name": "DONINGTON VALE",
                                  "registration_number": "2020/111111/07"})
        sync_vault_to_profile(b, {"company_name": "CAIROAI (PTY) LTD",
                                  "registration_number": "2020/222222/07"})

        profile_a = company_store.get_company_profile(a)
        profile_b = company_store.get_company_profile(b)

        assert resolve_value("company_name", profile_a)[0] == "DONINGTON VALE"
        assert resolve_value("registration_number", profile_a)[0] == "2020/111111/07"

        # The reported failure, stated as an assertion.
        assert resolve_value("company_name", profile_a)[0] != "CAIROAI (PTY) LTD"
        assert resolve_value("registration_number", profile_b)[0] != "2020/111111/07"
    finally:
        company_store.delete_company_profile(a)
        company_store.delete_company_profile(b)


def test_the_profile_read_is_scoped_by_company_id():
    """
    get_company_profile takes a company_id and returns only that company's row.
    A profile with no company behind it is empty, not somebody else's.
    """
    assert company_store.get_company_profile(f"never-{uuid.uuid4().hex[:8]}") == {}
