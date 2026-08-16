"""
One company cannot read or write another's tracked outcomes.

`/api/tracked-outcomes`, `/api/calendar-events` and `/api/compliance-status`
required no authentication and read whole tables with no tenant filter. They
returned [] only because the tables were empty — the moment a customer tracked
an outcome, every other customer could read it.

Two more of the same shape were found alongside them and are covered here:

- `/api/accuracy-stats` — unauthenticated `SELECT *` with no WHERE clause. An
  aggregate over every company's bids is still other companies' data.
- `/api/track-outcome` — an unauthenticated *write* whose lookup and UPDATE
  were keyed on prediction_id alone, so anyone who could guess or observe one
  could overwrite a stranger's recorded outcome.

Each test here fails if its WHERE clause is removed. That is the point of them:
the filter is invisible in the response when there is only one tenant in the
database, so only a second tenant proves it is there.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app import app

client = TestClient(app)

READ_ROUTES = [
    "/api/tracked-outcomes",
    "/api/calendar-events",
    "/api/compliance-status",
    "/api/accuracy-stats",
]


def _headers_for(company_id: str) -> dict:
    from agent import auth
    user = auth.create_user(
        f"xt-{uuid.uuid4().hex[:10]}@example.test", company_id,
        "test-suite-not-a-real-password")
    return {"Authorization": f"Bearer {auth.issue_session(user)}"}


@pytest.fixture(scope="module")
def alice():
    return _headers_for("pro_corp")


@pytest.fixture(scope="module")
def bob():
    return _headers_for("enterprise_corp")


# --- anonymous access ---------------------------------------------------------

@pytest.mark.parametrize("route", READ_ROUTES)
def test_anonymous_callers_are_refused(route):
    """The hole as it was: no credential, whole table."""
    assert client.get(route).status_code == 401, f"{route} still answers anonymously"


def test_anonymous_writes_are_refused():
    body = {"prediction_id": str(uuid.uuid4()), "actual_outcome": "won"}
    assert client.post("/api/track-outcome", json=body).status_code == 401


# --- cross-tenant reads -------------------------------------------------------

def test_one_company_does_not_see_anothers_tracked_outcome(alice, bob):
    prediction_id = f"xt-{uuid.uuid4().hex}"

    created = client.post("/api/track-outcome", headers=alice, json={
        "prediction_id": prediction_id,
        "tender_identifier": "XT-TENDER-1",
        "supplier_name": "ALICE CO",
        "actual_outcome": "won",
        "notes": "alice's private note",
    })
    assert created.status_code == 200, created.text

    mine = client.get("/api/tracked-outcomes", headers=alice).json()
    assert any(r["prediction_id"] == prediction_id for r in mine), "owner cannot see own row"

    theirs = client.get("/api/tracked-outcomes", headers=bob).json()
    assert all(r["prediction_id"] != prediction_id for r in theirs), (
        "another company can read this row — the tenant filter is not applied"
    )
    assert all("alice's private note" != (r.get("notes") or "") for r in theirs)


def test_accuracy_stats_are_not_computed_over_other_companies(alice, bob):
    """
    An aggregate is not anonymised data. With few customers a hit rate computed
    across all of them is close to reading their records.
    """
    def total_for(headers):
        return client.get("/api/accuracy-stats", headers=headers).json()["total_tracked"]

    # Measured as a delta, not an absolute: the local SQLite file persists
    # between runs, so both companies may already carry rows from a previous
    # one. What must hold is that a row written by Alice moves only Alice's
    # count.
    alice_before, bob_before = total_for(alice), total_for(bob)

    client.post("/api/track-outcome", headers=alice, json={
        "prediction_id": f"xt-{uuid.uuid4().hex}",
        "tender_identifier": "XT-TENDER-2",
        "supplier_name": "ALICE CO",
        "recommendation": "PURSUE",
        "actual_outcome": "won",
    })

    assert total_for(alice) == alice_before + 1, "the owner's own row is missing from their stats"
    assert total_for(bob) == bob_before, (
        "another company's totals moved when this company wrote a row — "
        "the aggregate spans tenants"
    )


# --- cross-tenant writes ------------------------------------------------------

def test_one_company_cannot_overwrite_anothers_outcome(alice, bob):
    """
    The lookup and UPDATE were keyed on prediction_id alone. Bob posting the
    same prediction_id used to edit Alice's row; now it can only create his own.
    """
    prediction_id = f"xt-{uuid.uuid4().hex}"

    client.post("/api/track-outcome", headers=alice, json={
        "prediction_id": prediction_id,
        "tender_identifier": "XT-TENDER-3",
        "supplier_name": "ALICE CO",
        "actual_outcome": "won",
        "notes": "alice original",
    })

    client.post("/api/track-outcome", headers=bob, json={
        "prediction_id": prediction_id,
        "tender_identifier": "XT-TENDER-3",
        "supplier_name": "BOB CO",
        "actual_outcome": "lost",
        "notes": "bob overwrote this",
    })

    alice_rows = client.get("/api/tracked-outcomes", headers=alice).json()
    row = next(r for r in alice_rows if r["prediction_id"] == prediction_id)

    assert row["actual_outcome"] == "won", "another company overwrote this outcome"
    assert row["notes"] == "alice original", "another company overwrote these notes"


# --- rows written before ownership existed ------------------------------------

def test_rows_with_no_owner_are_returned_to_nobody(alice):
    """
    tracked_outcomes predates company_id, so existing rows carry NULL. They
    belong to no one and must match no principal — visible-to-everyone is the
    failure this whole change is about.
    """
    from app import DB_PATH, _ensure_schema, _state_db

    orphan = f"orphan-{uuid.uuid4().hex}"
    with _state_db.connect(DB_PATH) as conn:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO tracked_outcomes (id, prediction_id, actual_outcome, company_id) "
            "VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), orphan, "won", None),
        )
        conn.commit()

    rows = client.get("/api/tracked-outcomes", headers=alice).json()
    assert all(r["prediction_id"] != orphan for r in rows), (
        "a row with no owner was served to a company"
    )
