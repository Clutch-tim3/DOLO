"""
A company can be set up from the app, and the confirmation gate survives it.

`/api/company-profile` was GET only. There was no PUT, no PATCH, no form: every
field the product depends on could only be written by running Python against
the database. That blocked both journeys — autofill fills bid forms FROM the
profile, and the quotation renderer takes its letterhead and signatory from it.

The risk in adding a write route is that it becomes a second way to write these
rows, with the confirmation gate reimplemented or quietly skipped. These rows
auto-fill real South African government tender documents; company_store's
docstring is explicit that `confirmed=True` asserts a human was shown specific
values and approved them, and must not be hard-coded by a caller that has shown
the user nothing.

So the route is two-step and passes `confirmed` through from the request. Most
of what is asserted here is that it cannot be talked past.
"""

import ast
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from agent import auth
from agent.memory import company_store
from app import app

client = TestClient(app)

APP_SOURCE = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")


@pytest.fixture
def headers():
    company_id = f"prof-{uuid.uuid4().hex[:10]}"
    user = auth.create_user(f"prof-{uuid.uuid4().hex[:8]}@example.test", company_id,
                            "profile-test-not-a-real-password")
    yield {"Authorization": f"Bearer {auth.issue_session(user)}"}, company_id
    company_store.delete_company_profile(company_id)


# --- the gate -----------------------------------------------------------------

def test_an_unconfirmed_write_changes_nothing(headers):
    """The default is refusal. A first call is a preview, not a write."""
    h, company_id = headers

    r = client.post("/api/company-profile", headers=h,
                    json={"fields": {"company_name": "Alpha Engineering"}})

    assert r.status_code == 200
    assert r.json()["written"] is False
    assert r.json()["status"] == "preview"
    assert company_store.get_company_profile(company_id) == {}, "an unconfirmed call wrote"


def test_the_preview_names_what_would_change(headers):
    """This is what the page puts in front of the person to obtain consent."""
    h, _ = headers
    r = client.post("/api/company-profile", headers=h,
                    json={"fields": {"company_name": "Alpha Engineering",
                                     "registration_number": "2019/111111/07"}})

    changes = {c["field"]: c["proposed"] for c in r.json()["changes"]}
    assert changes["company_name"] == "Alpha Engineering"
    assert changes["registration_number"] == "2019/111111/07"


def test_a_confirmed_write_is_applied(headers):
    h, company_id = headers
    r = client.post("/api/company-profile", headers=h,
                    json={"fields": {"company_name": "Alpha Engineering",
                                     "bbbee_level": 2},
                          "confirmed": True})

    assert r.status_code == 200
    assert r.json()["written"] is True

    stored = company_store.get_company_profile(company_id)
    assert stored["company_name"] == "Alpha Engineering"
    assert stored["bbbee_level"] == 2


def test_the_route_never_hard_codes_confirmed(headers):
    """
    The failure this guards against is a later edit passing confirmed=True to
    make the form "just work". Parsed rather than grepped, because the
    docstring discusses the flag at length.
    """
    tree = ast.parse(APP_SOURCE)
    func = next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "api_update_company_profile")

    body = func.body[1:] if ast.get_docstring(func) else func.body
    for node in body:
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            name = getattr(call.func, "attr", getattr(call.func, "id", ""))
            if name != "update_company_profile":
                continue
            for kw in call.keywords:
                if kw.arg == "confirmed":
                    # It may only be reached on the branch that read it from
                    # the request; a bare True literal is the bug.
                    assert not (isinstance(kw.value, ast.Constant)
                                and kw.value.value is True and _is_unguarded(func)), \
                        "confirmed=True is passed without the request asking for it"


def _is_unguarded(func) -> bool:
    """True if the function never reads `confirmed` from the request body."""
    return "confirmed" not in {
        n.value for n in ast.walk(func)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def test_the_gate_still_refuses_at_the_store_level(headers):
    """
    Belt and braces: even called directly, an unconfirmed write does nothing.
    The route is not the only thing standing between a value and a row.
    """
    _, company_id = headers
    result = company_store.update_company_profile(
        company_id, {"company_name": "Should Not Persist"}, confirmed=False)

    assert result["written"] is False
    assert company_store.get_company_profile(company_id) == {}


# --- what it refuses ----------------------------------------------------------

def test_a_signature_asset_is_refused(headers):
    """
    assert_no_signature_asset exists because a signature is never a profile
    field. CairoAI never signs anything.
    """
    h, _ = headers
    r = client.post("/api/company-profile", headers=h,
                    json={"fields": {"signature_image": "/tmp/sig.png"},
                          "confirmed": True})
    assert r.status_code == 400


def test_an_empty_body_is_refused(headers):
    h, _ = headers
    assert client.post("/api/company-profile", headers=h, json={}).status_code == 400
    assert client.post("/api/company-profile", headers=h,
                       json={"fields": {}}).status_code == 400


def test_an_anonymous_write_is_refused():
    """The route resolves a company, so it is company-aware and must be gated."""
    r = client.post("/api/company-profile",
                    json={"fields": {"company_name": "X"}, "confirmed": True})
    assert r.status_code == 401


def test_one_company_cannot_write_anothers_profile(headers):
    """
    The company comes from the credential, not the body — there is no
    company_id parameter to lie about.
    """
    h, company_id = headers
    other = f"victim-{uuid.uuid4().hex[:8]}"

    client.post("/api/company-profile", headers=h,
                json={"fields": {"company_name": "Mine", "company_id": other},
                      "confirmed": True})

    assert company_store.get_company_profile(other) == {}
    assert company_store.get_company_profile(company_id)["company_name"] == "Mine"


# --- the form's field list ----------------------------------------------------

def test_the_form_reads_its_fields_from_the_store(headers):
    """
    Duplicating the list in the page is the drift that left six fields
    unreachable from the agent (P0-3). The form asks the server instead.
    """
    h, _ = headers
    r = client.get("/api/company-profile/fields", headers=h)

    assert r.status_code == 200
    assert set(r.json()["writable_fields"]) == set(company_store.PROFILE_WRITABLE_FIELDS)
    for field in ("standard_cell", "tax_compliance_pin", "authorized_signatory_capacity"):
        assert field in r.json()["writable_fields"]


def test_the_field_list_reports_what_is_already_set(headers):
    h, company_id = headers
    client.post("/api/company-profile", headers=h,
                json={"fields": {"company_name": "Alpha"}, "confirmed": True})

    r = client.get("/api/company-profile/fields", headers=h)
    assert r.json()["profile_exists"] is True
    assert r.json()["values"]["company_name"] == "Alpha"
    assert r.json()["values"]["standard_cell"] is None
