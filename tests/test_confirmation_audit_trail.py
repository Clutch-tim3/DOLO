"""
A confirmation records who made it, and the record is tamper-evident.

Confirmation is the gate that makes drafts-only mean anything. Before this, the
record could say a field was acknowledged at a time with a note — but not by
whom. On a document submitted to an organ of state that trail is the evidence a
person reviewed it, and "someone at this company clicked" is not that.

The actor is inside the MAC, not merely beside it. A name in an unsigned column
is a label anyone with database access can change, and evidence that can be
rewritten is not evidence.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_autofill.integration.stamp_signing import (
    ack_mac,
    ack_payload,
    matches,
    sign,
    values_payload,
)


# --- the actor is covered by the signature ------------------------------------

def test_two_users_acknowledging_the_same_thing_sign_differently():
    """If the actor were outside the MAC these would collide, and swapping the
    name in the database would leave a valid signature."""
    a = ack_mac("rev-1", "F01", "2026-08-16T10:00:00", "checked the CIDB grade", "user-alice")
    b = ack_mac("rev-1", "F01", "2026-08-16T10:00:00", "checked the CIDB grade", "user-bob")
    assert a != b


def test_rewriting_who_acknowledged_invalidates_the_signature():
    payload_args = ("rev-1", "F01", "2026-08-16T10:00:00", "checked it")
    real = ack_mac(*payload_args, "user-alice")

    # The forgery: same field, same time, same note, different person.
    assert not matches(real, ack_payload(*payload_args, "user-bob"))
    assert matches(real, ack_payload(*payload_args, "user-alice"))


def test_an_unattributed_acknowledgement_does_not_verify_as_an_attributed_one():
    """v1 acknowledgements carried no actor. They must not pass as v2."""
    unattributed = ack_mac("rev-1", "F01", "2026-08-16T10:00:00", "checked it", "")
    assert not matches(unattributed, ack_payload("rev-1", "F01", "2026-08-16T10:00:00",
                                                 "checked it", "user-alice"))


def test_the_value_confirmation_also_binds_its_actor():
    pairs = [("F01", "Company name", "ALPHA ENGINEERING")]
    a = sign(values_payload("co-1", "rev-1", pairs, "2026-08-16T10:00:00", "user-alice"))
    b = sign(values_payload("co-1", "rev-1", pairs, "2026-08-16T10:00:00", "user-bob"))
    assert a != b


def test_company_id_alone_was_never_enough():
    """
    values_payload already covered company_id. That says which tenant, not
    which person — two users at the same company produced identical
    signatures, so the record could not distinguish them.
    """
    pairs = [("F01", "Company name", "ALPHA ENGINEERING")]
    same_company_different_people = {
        sign(values_payload("co-1", "rev-1", pairs, "2026-08-16T10:00:00", user))
        for user in ("user-alice", "user-bob")
    }
    assert len(same_company_different_people) == 2


def test_the_payload_version_moved_so_old_macs_fail_closed():
    """
    Adding the actor changes the payload. Existing signatures must stop
    verifying rather than silently covering less than they appear to.
    """
    assert ack_payload("r", "k", "t", "n", "u")["v"] == 2
    assert values_payload("c", "r", [], "t", "u")["v"] == 2


# --- the trail reaches the caller ---------------------------------------------

def test_acknowledge_field_accepts_and_returns_the_actor():
    import inspect
    from agent_autofill.integration.review_gate import acknowledge_field

    params = inspect.signature(acknowledge_field).parameters
    assert "user_id" in params, "there is no way to record who acknowledged a field"
    assert "username" in params


def test_confirm_filled_values_accepts_the_actor():
    import inspect
    from agent_autofill.integration.review_gate import confirm_filled_values

    params = inspect.signature(confirm_filled_values).parameters
    assert "user_id" in params
    assert "username" in params


def test_the_routes_take_the_actor_from_the_principal_not_the_body():
    """
    A self-reported actor in an audit trail is worth nothing. The identity must
    come from the verified session.
    """
    source = (
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "agent_autofill", "pack_api.py")
    )
    with open(source, encoding="utf-8") as f:
        body = f.read()

    assert "user_id=principal.user_id" in body
    assert 'user_id=(body or {}).get' not in body, "the actor is read from the request body"
