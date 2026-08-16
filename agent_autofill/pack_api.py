"""
HTTP surface for the Autofill Vault.

A self-contained `APIRouter`, mounted from app.py in one line — the same shape
`questionnaire_api.py` uses, and for the same reason: route registration belongs
to the integration owner, and app.py is already 1,900 lines.

THE PATHS ARE FIXED. A frontend agent is building against them in parallel:

    POST   /api/autofill-packs
    PATCH  /api/autofill-packs/{pack_id}
    POST   /api/autofill-packs/{pack_id}/files
    DELETE /api/autofill-packs/{pack_id}/files/{file_id}
    POST   /api/autofill-packs/{pack_id}/submit
    GET    /api/autofill-packs/{pack_id}/status
    GET    /api/autofill-packs
    GET    /api/autofill-packs/{pack_id}
    POST   /api/autofill-packs/{pack_id}/acknowledge
    POST   /api/autofill-packs/{pack_id}/confirm-values
    POST   /api/autofill-packs/{pack_id}/export

TENANCY
-------
Every route depends on `require_principal`, so an anonymous request is a 401 and
never somebody. The company comes from the principal and nothing else: there is
no `company_id` in any body or query here, so there is no value for a client to
lie about. `assert_company` is used where a body may carry one anyway, so a
client asking to act as another tenant is told no rather than quietly served its
own data.

A pack belonging to another company answers **404, not 403**. 403 would confirm
the id exists to someone holding only an id.

WHY SUBMIT RETURNS BEFORE THE WORK IS DONE
------------------------------------------
Filling a pack of returnable forms means one Haiku call and a full
extract-and-fill per document. That does not fit inside a request, so submit
claims the pack (a conditional UPDATE to `processing`) and hands the work to a
BackgroundTask. The STATE is in the database, not in a dict: `BATCH_JOBS` in
app.py is process-local and a Cloud Functions instance restart loses it, which
for a pack would mean a review the user can see and cannot finish.
"""

from __future__ import annotations

import logging

from fastapi import (APIRouter, BackgroundTasks, Depends, File, HTTPException,
                     Query, Request, UploadFile)

from agent.auth import Principal, assert_company, require_principal
from agent_autofill.integration import pack_events, pack_store
from agent_autofill.integration.pack_store import (
    PackInputError,
    PackNotFound,
    PackRefused,
    PackStateError,
)
from agent_autofill.integration.review_gate import ReviewGateError

log = logging.getLogger("agent_autofill.pack_api")

router = APIRouter(prefix="/api/autofill-packs", tags=["agent-autofill"])


def _company(principal: Principal, claimed=None) -> str:
    """
    The authenticated tenant. `claimed` is whatever a body happened to carry.

    Routes here never *need* a company_id from the client, but a frontend that
    sends one out of habit should get a clear refusal rather than have it
    silently ignored — an ignored mismatch reads as success to the caller.
    """
    return assert_company(principal, claimed)


def _fail(exc: Exception):
    """
    Map a pack refusal onto a status code.

    `PackStateError` carries a JSON body for the export refusals — those have to
    name what is outstanding, which does not fit in a string — and a plain
    sentence for everything else.
    """
    if isinstance(exc, PackNotFound):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PackInputError):
        raise HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, PackRefused):
        # The plan does not include Agent Autofill, or the day's allowance is
        # spent. 403 rather than 409: nothing about the pack is wrong.
        raise HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, PackStateError):
        detail = str(exc)
        if detail.startswith("{"):
            import json

            try:
                detail = json.loads(detail)
            except ValueError:
                pass
        raise HTTPException(status_code=409, detail=detail)
    if isinstance(exc, ReviewGateError):
        raise HTTPException(status_code=400, detail=str(exc))
    raise exc


# --- create / edit ----------------------------------------------------------


@router.post("")
async def api_create_pack(request: Request,
                          principal: Principal = Depends(require_principal)):
    """Start an empty pack. Returns the id every other route is keyed on."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        # A create with no body is the common case from a "New pack" button.
        body = {}
    company_id = _company(principal, (body or {}).get("company_id"))
    try:
        return pack_store.create_pack(company_id, (body or {}).get("pack_name", ""))
    except (PackNotFound, PackStateError, PackRefused, PackInputError) as e:
        _fail(e)


@router.patch("/{pack_id}")
async def api_rename_pack(pack_id: str, request: Request,
                          principal: Principal = Depends(require_principal)):
    body = await request.json()
    company_id = _company(principal, (body or {}).get("company_id"))
    try:
        return pack_store.rename_pack(company_id, pack_id, (body or {}).get("pack_name", ""))
    except (PackNotFound, PackStateError, PackRefused, PackInputError) as e:
        _fail(e)


@router.post("/{pack_id}/files")
async def api_add_files(pack_id: str,
                        files: list[UploadFile] = File(...),
                        principal: Principal = Depends(require_principal)):
    """
    Add SEVERAL documents to one pack in ONE request.

    The field name is `files` and it repeats. A tender's returnable forms are
    uploaded as a bundle, and one request per file would make a half-uploaded
    pack the normal outcome of a flaky connection.
    """
    company_id = _company(principal)
    uploads = []
    for upload in files:
        content = await upload.read()
        uploads.append((upload.filename or "document", content))
    try:
        return pack_store.add_files(company_id, pack_id, uploads)
    except (PackNotFound, PackStateError, PackRefused, PackInputError) as e:
        _fail(e)


@router.delete("/{pack_id}/files/{file_id}")
async def api_remove_file(pack_id: str, file_id: str,
                          principal: Principal = Depends(require_principal)):
    company_id = _company(principal)
    try:
        return pack_store.remove_file(company_id, pack_id, file_id)
    except (PackNotFound, PackStateError, PackRefused, PackInputError) as e:
        _fail(e)


# --- submit and poll --------------------------------------------------------


@router.post("/{pack_id}/submit")
async def api_submit_pack(pack_id: str, background: BackgroundTasks,
                          principal: Principal = Depends(require_principal)):
    """
    Claim the pack and start the work.

    `submit_pack` runs the tier/quota check — the same free COUNT that
    `run_autofill` runs as its own first step — so a plan without Agent Autofill
    is refused here, before a background task exists and long before anything is
    classified. No Claude call happens on the refused path.
    """
    company_id = _company(principal)
    try:
        claimed = pack_store.submit_pack(company_id, pack_id)
    except (PackNotFound, PackStateError, PackRefused, PackInputError) as e:
        _fail(e)

    background.add_task(pack_store.process_pack, company_id, pack_id)
    return claimed


@router.get("/{pack_id}/status")
async def api_pack_status(pack_id: str,
                          since: int = Query(default=0, ge=0),
                          principal: Principal = Depends(require_principal)):
    """
    Poll one pack.

    `since` is the highest event `seq` the caller already has, so a client
    reconnecting mid-run resumes the narration rather than replaying it from
    the top. The pack row is loaded first, which is what enforces ownership —
    the events are keyed only by pack_id, so reading them before that check
    would leak another tenant's progress.
    """
    company_id = _company(principal)
    try:
        payload = pack_store.pack_status(company_id, pack_id)
        payload["events"] = pack_events.events_since(pack_id, since)
        payload["usage"] = pack_events.token_totals(pack_id)
        return payload
    except (PackNotFound, PackStateError, PackRefused, PackInputError) as e:
        _fail(e)


# --- read -------------------------------------------------------------------


@router.get("")
async def api_list_packs(principal: Principal = Depends(require_principal)):
    try:
        return pack_store.list_packs(principal.company_id)
    except (PackNotFound, PackStateError, PackRefused, PackInputError) as e:
        _fail(e)


@router.get("/{pack_id}")
async def api_pack_detail(pack_id: str,
                          principal: Principal = Depends(require_principal)):
    """Pack fields, the aggregated flags, and every document's review summary."""
    company_id = _company(principal)
    try:
        return pack_store.pack_detail(company_id, pack_id)
    except (PackNotFound, PackStateError, PackRefused, PackInputError) as e:
        _fail(e)


# --- review actions ---------------------------------------------------------


@router.post("/{pack_id}/acknowledge")
async def api_acknowledge(pack_id: str, request: Request,
                          principal: Principal = Depends(require_principal)):
    """
    Acknowledge ONE flagged field, in one of the pack's documents.

    Still one field per call, with the person's own note. Grouping documents
    into a pack does not create a way to clear them all at once — that is the
    whole difference between a review and a checkbox.
    """
    body = await request.json()
    company_id = _company(principal, (body or {}).get("company_id"))
    try:
        return pack_store.acknowledge(
            company_id, pack_id,
            (body or {}).get("review_id", ""),
            (body or {}).get("item_key", ""),
            (body or {}).get("note", ""),
            # A7: who. Taken from the verified principal, never from the body —
            # a self-reported actor in an audit trail is worth nothing.
            user_id=principal.user_id,
            username=principal.username,
        )
    except (PackNotFound, PackStateError, PackRefused, PackInputError, ReviewGateError) as e:
        _fail(e)


@router.post("/{pack_id}/confirm-values")
async def api_confirm_values(pack_id: str, request: Request,
                             principal: Principal = Depends(require_principal)):
    """
    Confirm the values CairoAI pre-filled into ONE of the pack's documents.

    `confirmed_keys` must match that document's stored set exactly. A partial
    list is refused rather than intersected — silently confirming the overlap is
    how a confirmation stops meaning anything.
    """
    body = await request.json()
    company_id = _company(principal, (body or {}).get("company_id"))
    keys = (body or {}).get("confirmed_keys")
    try:
        return pack_store.confirm_values(
            company_id, pack_id, (body or {}).get("review_id", ""), keys,
            user_id=principal.user_id, username=principal.username)
    except (PackNotFound, PackStateError, PackRefused, PackInputError, ReviewGateError) as e:
        _fail(e)


@router.post("/{pack_id}/export")
async def api_export_pack(pack_id: str,
                          principal: Principal = Depends(require_principal)):
    """
    Export the pack. 409 naming what is outstanding unless it is `reviewed`.

    The 409 body carries the outstanding flags and the documents whose values
    are still unconfirmed, so the frontend can send the user straight to them
    rather than saying "not ready".
    """
    company_id = _company(principal)
    try:
        return pack_store.export_pack(company_id, pack_id)
    except (PackNotFound, PackStateError, PackRefused, PackInputError, ReviewGateError) as e:
        _fail(e)
