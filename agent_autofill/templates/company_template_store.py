"""CRUD for the Agent Autofill company template.

THIS MODULE OWNS NO STORAGE.

There is deliberately no `import sqlite3` here and no second table. Every read
goes through ``agent.memory.company_store.get_company_profile`` and every write
goes through ``agent.memory.company_store.update_company_profile``. The
questionnaire is a *view* over the canonical profile, not a copy of it.

Two things follow from that, and both are the point:

* ``get_company_profile`` remains the single source of truth. Company facts are
  never taken from an LLM's conversational context -- not from what the model
  "recalls" the user saying, not from a summary, not from a previous tool
  result. If a value is needed, the row is read. A model that has seen a
  registration number once will reproduce a confident, wrong one on request.

* The confirmation gate in ``update_company_profile`` cannot be routed around by
  using this module instead. ``save_questionnaire`` has the same
  ``confirmed=False`` default and forwards it; there is no privileged write
  path.
"""

from __future__ import annotations

from agent.memory.company_store import (
    get_company_profile,
    update_company_profile,
    delete_company_profile,
    preview_company_profile_update,
    assert_no_signature_asset,
    SignatureAssetRefused,
)

from agent_autofill.templates.questionnaire_schema import (
    QUESTIONNAIRE_FIELDS,
    FIELDS_BY_KEY,
    REQUIRED_FIELDS,
    CANONICAL_TO_PROFILE_COLUMN,
    NEVER_AUTOFILL_FROM_PROFILE,
    validate_answers,
    answers_to_profile_fields,
    profile_to_answers,
    describe,
)

__all__ = [
    "get_questionnaire_definition",
    "load_questionnaire",
    "preview_questionnaire_save",
    "save_questionnaire",
    "get_autofill_values",
    "questionnaire_completeness",
    "delete_company_template",
]


def get_questionnaire_definition() -> dict:
    """The form definition the wizard renders. Pure data, no company involved."""
    return describe()


def load_questionnaire(company_id: str) -> dict:
    """
    Read the stored answers for a company.

    Reads the canonical profile row -- nothing else is consulted.
    """
    profile = get_company_profile(company_id)
    answers = profile_to_answers(profile)
    return {
        "company_id": company_id,
        "profile_exists": bool(profile),
        "answers": answers,
        "completeness": _completeness_from_answers(answers),
        "source": "company_profile table via company_store.get_company_profile",
    }


def _completeness_from_answers(answers: dict) -> dict:
    filled, missing = [], []
    for spec in QUESTIONNAIRE_FIELDS:
        value = (answers or {}).get(spec.key)
        has_value = value not in (None, "", [], {})
        (filled if has_value else missing).append(spec.key)

    missing_required = [k for k in REQUIRED_FIELDS if k in missing]
    return {
        "total_fields": len(QUESTIONNAIRE_FIELDS),
        "filled": len(filled),
        "missing": missing,
        "missing_required": missing_required,
        "ready_for_autofill": not missing_required,
    }


def questionnaire_completeness(company_id: str) -> dict:
    return load_questionnaire(company_id)["completeness"]


def preview_questionnaire_save(company_id: str, answers: dict, partial: bool = False) -> dict:
    """
    Validate answers and show exactly what would change -- writing nothing.

    This is what the wizard's review step and the agent's confirmation prompt
    are built on: the user has to see the concrete before/after before the
    write is allowed to happen.
    """
    validation = validate_answers(answers, partial=partial)
    if not validation["valid"]:
        return {
            "status": "invalid",
            "written": False,
            "errors": validation["errors"],
            "warnings": validation["warnings"],
            "missing_required": validation["missing_required"],
            "changes": [],
        }

    profile_fields = answers_to_profile_fields(validation["cleaned"])

    try:
        preview = preview_company_profile_update(company_id, profile_fields)
    except SignatureAssetRefused as e:
        return {
            "status": "refused",
            "reason": "signature_asset",
            "written": False,
            "errors": {"authorized_signatory_name": str(e)},
            "warnings": [],
            "missing_required": [],
            "changes": [],
        }

    return {
        "status": "ok",
        "written": False,
        "errors": {},
        "warnings": validation["warnings"],
        "missing_required": validation["missing_required"],
        "changes": preview["changes"],
        "no_op": preview["no_op"],
    }


def save_questionnaire(company_id: str, answers: dict, confirmed: bool = False, partial: bool = False) -> dict:
    """
    Persist questionnaire answers to the canonical profile.

    REFUSES unless `confirmed=True`, in exactly the same way and for exactly the
    same reason as `update_company_profile`. `confirmed` is not defaulted to
    True anywhere in this package: the caller must have shown a human the values
    from `preview_questionnaire_save` and received an answer.

    Validation runs before the gate, so an unconfirmed call still tells the user
    what is wrong with their input -- it just does not write it.
    """
    validation = validate_answers(answers, partial=partial)
    if not validation["valid"]:
        return {
            "status": "invalid",
            "written": False,
            "errors": validation["errors"],
            "warnings": validation["warnings"],
            "missing_required": validation["missing_required"],
        }

    profile_fields = answers_to_profile_fields(validation["cleaned"])

    try:
        assert_no_signature_asset(profile_fields)
    except SignatureAssetRefused as e:
        return {"status": "refused", "reason": "signature_asset", "written": False, "message": str(e)}

    # The gate lives in company_store; this forwards `confirmed` untouched.
    result = update_company_profile(company_id, profile_fields, confirmed=confirmed)
    result["warnings"] = validation["warnings"]
    return result


def get_autofill_values(company_id: str) -> dict:
    """
    Canonical-field -> value map for the fill engine.

    Keys are the canonical names from
    ``agent_autofill.extraction.field_alias_dictionary.CANONICAL_FIELDS``, so a
    matched form label maps straight onto a value.

    Anything in NEVER_AUTOFILL_FROM_PROFILE is excluded by construction, and
    excluded again defensively below. `signature` is the one that matters:
    the draft must reach the human with the signature box still empty.
    """
    profile = get_company_profile(company_id)
    if not profile:
        return {"company_id": company_id, "values": {}, "missing": list(CANONICAL_TO_PROFILE_COLUMN)}

    values, missing = {}, []
    for canonical, column in CANONICAL_TO_PROFILE_COLUMN.items():
        if canonical in NEVER_AUTOFILL_FROM_PROFILE:
            continue
        value = profile.get(column)
        if value in (None, "", [], {}):
            missing.append(canonical)
        else:
            values[canonical] = value

    return {
        "company_id": company_id,
        "values": values,
        "missing": missing,
        # Explicit, so a caller reading this dict can see the boundary rather
        # than having to know about it.
        "never_autofilled": sorted(NEVER_AUTOFILL_FROM_PROFILE),
        "signatory_name": profile.get("authorized_signatory_name"),
        "signatory_note": (
            "authorized_signatory_name is a NAME ONLY. Print it beside the "
            "signature line; never render, stamp or otherwise apply a signature."
        ),
    }


def delete_company_template(company_id: str) -> dict:
    """Delete a company's profile row. Used by tests to clean up after themselves."""
    return delete_company_profile(company_id)
