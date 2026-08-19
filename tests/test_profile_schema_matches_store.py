"""
The agent is told about every field it can actually write.

`tools_schema.py` advertised 17 of the 23 entries in PROFILE_WRITABLE_FIELDS.
The model only knows what the schema tells it, so asking the agent to set a
cell number or a tax compliance PIN got a refusal or silence — for fields the
store accepts and the SBD 1 filler needs.

The six missing were standard_cell, standard_fax, tax_compliance_pin,
authorized_signatory_capacity, brand_colour and tagline. The first three are
why a filled MBD 1 still showed blanks: the form asks for each on its own row.

This is a drift test. Two lists in two files that must agree, with no mechanism
keeping them together — the failure is silent and shows up as a blank on a
submitted government form.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent.memory.company_store import PROFILE_WRITABLE_FIELDS
from agent.memory import tools_schema


def _fields_description() -> str:
    """The description string the model actually receives for `fields`."""
    for tool in _all_tools():
        schema = tool.get("input_schema") or tool.get("parameters") or {}
        props = schema.get("properties") or {}
        if tool.get("name") == "update_company_profile" and "fields" in props:
            return props["fields"].get("description", "")
    raise AssertionError("update_company_profile is not in the tool schema")


def _all_tools() -> list:
    out = []
    for value in vars(tools_schema).values():
        if isinstance(value, list):
            out.extend(v for v in value if isinstance(v, dict) and "name" in v)
        elif isinstance(value, dict) and "name" in value:
            out.append(value)
    return out


def test_every_writable_field_is_advertised():
    """
    The drift itself. A field the store accepts but the schema omits is a
    capability the product has and the agent cannot reach.
    """
    described = _fields_description()
    missing = [f for f in PROFILE_WRITABLE_FIELDS if f not in described]
    assert not missing, (
        f"{len(missing)} writable field(s) the agent is never told about: {missing}"
    )


def test_the_schema_advertises_nothing_the_store_would_ignore():
    """
    The other direction. update_company_profile silently drops unknown keys, so
    a field advertised but not writable is an instruction the model will follow
    into a no-op — and it will report success.
    """
    described = _fields_description()
    # Only check plausible field-shaped tokens, not prose.
    import re
    tokens = set(re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", described))
    known = set(PROFILE_WRITABLE_FIELDS) | {
        # Sub-keys of `directors`, described in the same string.
        "id_number", "is_state_employee",
        # Referred to by name in the warnings.
        "pending_changes",
    }
    invented = tokens - known
    assert not invented, f"schema names fields the store does not accept: {sorted(invented)}"


@pytest.mark.parametrize("field", [
    "standard_cell", "standard_fax", "tax_compliance_pin",
    "authorized_signatory_capacity", "brand_colour", "tagline",
])
def test_the_six_that_were_missing(field):
    """Named individually so a regression says which one went."""
    assert field in _fields_description()


# --- the warnings that must survive -------------------------------------------

def test_the_signature_warning_is_intact():
    """
    Load-bearing: it is what stops the model treating a signatory name as
    permission to produce a signature. CairoAI never signs anything.
    """
    described = _fields_description()
    assert "NAME ONLY" in described
    assert "never signs anything" in described


def test_the_directors_declaration_warning_is_intact():
    """
    is_state_employee is a sworn SBD 4 declaration. A model assuming it is
    answering a legal question on someone's behalf.
    """
    described = _fields_description()
    assert "is_state_employee" in described
    assert "never assumed" in described


def test_the_tax_pin_is_not_presented_as_something_to_generate():
    """It is a SARS reference the buyer verifies against, not a format to fill."""
    described = _fields_description()
    assert "tax_compliance_pin" in described
    assert "never invent" in described.lower()


def test_the_confirmation_gate_is_still_described():
    """
    update_company_profile refuses without confirmed=True. If the model is not
    told that, it cannot drive the flow that makes the gate usable.
    """
    for tool in _all_tools():
        if tool.get("name") != "update_company_profile":
            continue
        schema = tool.get("input_schema") or tool.get("parameters") or {}
        confirmed = (schema.get("properties") or {}).get("confirmed", {})
        assert "explicitly approved" in confirmed.get("description", "")
        return
    raise AssertionError("update_company_profile is not in the tool schema")
