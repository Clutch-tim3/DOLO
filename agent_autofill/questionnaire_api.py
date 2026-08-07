"""FastAPI router for the company questionnaire wizard.

Deliberately a self-contained `APIRouter` rather than routes added to app.py:
route registration belongs to the integration owner. Mounting is one line:

    from agent_autofill.questionnaire_api import router as questionnaire_router
    app.include_router(questionnaire_router)

Tenant identity comes from the X-Company-ID header, matching what app.py's other
company-aware endpoints already do. It is never taken from the request body,
because the body is the part an untrusted page can set freely.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from agent_autofill.templates.company_template_store import (
    get_questionnaire_definition,
    load_questionnaire,
    preview_questionnaire_save,
    save_questionnaire,
    get_autofill_values,
)

router = APIRouter(prefix="/api/questionnaire", tags=["agent-autofill"])

DEFAULT_COMPANY_ID = "starter_corp"


def _company_id(request: Request) -> str:
    return request.headers.get("X-Company-ID", DEFAULT_COMPANY_ID)


@router.get("/definition")
async def api_questionnaire_definition():
    """Steps, fields, validation hints. No company data."""
    return get_questionnaire_definition()


@router.get("")
async def api_questionnaire_load(request: Request):
    """Current stored answers, read from the canonical profile row."""
    return load_questionnaire(_company_id(request))


@router.post("/preview")
async def api_questionnaire_preview(request: Request):
    """
    Validate and diff. Writes nothing.

    This is the call the wizard's review step makes so the user can see exactly
    what is about to change before they are asked to confirm it.
    """
    body = await request.json()
    result = preview_questionnaire_save(
        _company_id(request),
        body.get("answers") or {},
        partial=bool(body.get("partial")),
    )
    status = 200 if result["status"] == "ok" else 422
    return JSONResponse(result, status_code=status)


@router.post("/save")
async def api_questionnaire_save(request: Request):
    """
    Commit answers. Requires `confirmed: true` in the body.

    The flag records that a human clicked the confirm control after seeing the
    preview. It is forwarded, never defaulted -- an omitted flag comes back as
    HTTP 409 with the pending diff rather than as a silent write.
    """
    body = await request.json()
    result = save_questionnaire(
        _company_id(request),
        body.get("answers") or {},
        confirmed=bool(body.get("confirmed")),
        partial=bool(body.get("partial")),
    )

    if result.get("status") == "confirmation_required":
        return JSONResponse(result, status_code=409)
    if result.get("status") in ("invalid", "refused"):
        return JSONResponse(result, status_code=422)
    return JSONResponse(result, status_code=200)


@router.get("/autofill-values")
async def api_autofill_values(request: Request):
    """Canonical-field -> value map for the fill engine. Signature excluded."""
    return get_autofill_values(_company_id(request))
