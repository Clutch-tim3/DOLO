"""
Agent Autofill: company profile schema, the confirmation gate, and the
questionnaire round-trip.

DATABASE ISOLATION
------------------
`AGENT_DB_DIR` is set before any `agent.*` import. `agent/db_paths.py` reads it
at import time and resolves every SQLite path into that directory, seeding it
with a copy of the real agent_memory.db. So these tests run against a full copy
of production data -- which is what makes the migration assertions meaningful --
without being able to write to the real file.

Run standalone:   python tests/test_agent_autofill_profile.py
Run under pytest: pytest tests/test_agent_autofill_profile.py -v
"""

import ast
import os
import sys
import shutil
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# MUST happen before importing anything under agent/.
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="cairo-autofill-test-"))
os.environ["AGENT_DB_DIR"] = str(_TEST_DB_DIR)

from agent.memory import company_store                                    # noqa: E402
from agent.memory.company_store import (                                  # noqa: E402
    get_company_profile,
    update_company_profile,
    delete_company_profile,
    assert_no_signature_asset,
    SignatureAssetRefused,
    _migrate_company_profile,
    _PROFILE_MIGRATIONS,
)
from agent.tool_dispatch import TOOL_REGISTRY, execute_tool               # noqa: E402
from agent_autofill.templates import questionnaire_schema as qs           # noqa: E402
from agent_autofill.templates import company_template_store as cts        # noqa: E402

TEST_COMPANY = "autofill_test_co_ZZZ"

# The original pre-migration column list, kept literal so the test does not
# simply restate whatever the code currently does.
LEGACY_COLUMNS = [
    "company_id", "company_name", "registration_number", "csd_number",
    "bbbee_level", "province", "registered_municipality", "industry",
    "logo_file_path", "created_at", "updated_at",
]

LEGACY_CREATE = """
CREATE TABLE company_profile (
    company_id TEXT PRIMARY KEY,
    company_name TEXT,
    registration_number TEXT,
    csd_number TEXT,
    bbbee_level INTEGER,
    province TEXT,
    registered_municipality TEXT,
    industry TEXT,
    logo_file_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

VALID_ANSWERS = {
    "company_name": "Molwantwa Civils (Pty) Ltd",
    "registration_number": "2019/443221/07",
    "csd_number": "MAAA0774312",
    "bbbee_level": "Level 2",
    "province": "Gauteng",
    "registered_municipality": "City of Ekurhuleni",
    "industry": "Civil Engineering & Roadworks",
    "tax_reference_number": "9130447165",
    "vat_registration_number": "4880156723",
    "physical_address": "14 Rand Airport Road, Germiston, 1401",
    "postal_address": "PO Box 771, Germiston, 1400",
    "standard_contact_person": "Naledi Molwantwa",
    "standard_phone": "+27 11 873 4410",
    "standard_email": "bids@molwantwacivils.co.za",
    "authorized_signatory_name": "Naledi Molwantwa",
    "directors": [
        {"name": "Naledi Molwantwa", "id_number": "8203015009089", "is_state_employee": False},
        {"name": "Sipho Khumalo", "id_number": "7906225043083", "is_state_employee": True},
    ],
}


def _cleanup():
    delete_company_profile(TEST_COMPANY)


# ===========================================================================
# 1. Migration is additive and non-destructive
# ===========================================================================

def test_migration_is_additive_and_non_destructive():
    """
    Build a database with the ORIGINAL schema, put a row in it, migrate, and
    require that every original value is byte-identical afterwards.
    """
    tmp = Path(tempfile.mkdtemp(prefix="cairo-legacy-")) / "legacy.db"
    conn = sqlite3.connect(tmp)
    conn.executescript(LEGACY_CREATE)
    original = (
        "pro_corp", "CairoAI", "2026/250499/07", None, "Level 1 Contributor",
        "Gauteng", "City of Tshwane", "ICT & Professional Services", None,
        "2026-07-30 16:27:22.591706", "2026-07-30 16:27:22.593707",
    )
    conn.execute(
        "INSERT INTO company_profile VALUES (?,?,?,?,?,?,?,?,?,?,?)", original
    )
    conn.commit()

    before_cols = [r[1] for r in conn.execute("PRAGMA table_info(company_profile)")]
    conn.row_factory = sqlite3.Row
    before_row = dict(conn.execute("SELECT * FROM company_profile").fetchone())
    assert before_cols == LEGACY_COLUMNS

    added = _migrate_company_profile(conn)
    conn.commit()

    after_cols = [r[1] for r in conn.execute("PRAGMA table_info(company_profile)")]
    after_row = dict(conn.execute("SELECT * FROM company_profile").fetchone())

    # Every original column survives, in the same position.
    assert after_cols[: len(LEGACY_COLUMNS)] == LEGACY_COLUMNS
    # Every new column arrived.
    assert added == [c for c, _ in _PROFILE_MIGRATIONS]
    # Every original value is unchanged.
    for col in LEGACY_COLUMNS:
        assert after_row[col] == before_row[col], f"{col} changed during migration"
    # New columns are NULL, not defaulted to something invented.
    for col, _ in _PROFILE_MIGRATIONS:
        assert after_row[col] is None

    # Re-running is a no-op, not an error.
    assert _migrate_company_profile(conn) == []

    conn.close()
    shutil.rmtree(tmp.parent, ignore_errors=True)


def test_seeded_real_rows_survived_migration():
    """The seeded copy of the real database still has its real values."""
    profile = get_company_profile("pro_corp")
    assert profile.get("company_name") == "CairoAI"
    assert profile.get("registration_number") == "2026/250499/07"
    assert profile.get("bbbee_level") == "Level 1 Contributor"
    assert "authorized_signatory_name" in profile


# ===========================================================================
# 2. The confirmation gate
# ===========================================================================

def test_unconfirmed_write_is_refused_and_writes_nothing():
    _cleanup()
    result = update_company_profile(TEST_COMPANY, {"company_name": "Ghost Corp"})

    assert result["status"] == "confirmation_required"
    assert result["written"] is False
    assert result["pending_changes"] == [
        {"field": "company_name", "current": None, "proposed": "Ghost Corp"}
    ]
    # Not even the "create the row if missing" INSERT happened.
    assert get_company_profile(TEST_COMPANY) == {}


def test_unconfirmed_write_cannot_overwrite_an_existing_value():
    _cleanup()
    update_company_profile(TEST_COMPANY, {"company_name": "Real Name"}, confirmed=True)

    result = update_company_profile(TEST_COMPANY, {"company_name": "Hallucinated Name"})
    assert result["status"] == "confirmation_required"
    assert result["written"] is False
    assert get_company_profile(TEST_COMPANY)["company_name"] == "Real Name"
    _cleanup()


def test_confirmed_write_persists():
    _cleanup()
    result = update_company_profile(
        TEST_COMPANY, {"company_name": "Real Name"}, confirmed=True
    )
    assert result["status"] == "success"
    assert result["written"] is True
    assert get_company_profile(TEST_COMPANY)["company_name"] == "Real Name"
    _cleanup()


def test_tool_registry_write_defaults_to_refusal():
    """
    The path the agent actually uses. A model that emits update_company_profile
    without `confirmed` gets a refusal, not a write.
    """
    _cleanup()
    result, is_error = execute_tool(
        "update_company_profile",
        {"company_id": TEST_COMPANY, "fields": {"vat_registration_number": "4880156723"}},
        TEST_COMPANY,
    )
    assert is_error is False          # a refusal is a normal result, not a crash
    assert result["status"] == "confirmation_required"
    assert result["written"] is False
    assert get_company_profile(TEST_COMPANY) == {}


def test_tool_registry_write_succeeds_when_confirmed():
    _cleanup()
    result, is_error = execute_tool(
        "update_company_profile",
        {
            "company_id": TEST_COMPANY,
            "fields": {"vat_registration_number": "4880156723"},
            "confirmed": True,
        },
        TEST_COMPANY,
    )
    assert is_error is False
    assert result["status"] == "success"
    assert get_company_profile(TEST_COMPANY)["vat_registration_number"] == "4880156723"
    _cleanup()


def test_template_store_save_has_the_same_gate():
    """company_template_store is not a privileged back door around the gate."""
    _cleanup()
    result = cts.save_questionnaire(TEST_COMPANY, VALID_ANSWERS)
    assert result["status"] == "confirmation_required"
    assert result["written"] is False
    assert get_company_profile(TEST_COMPANY) == {}


def test_resaving_identical_values_is_a_no_op():
    """
    Regression. The diff normaliser used to json.loads() every string, so
    "4880156723" became the integer 4880156723 and compared unequal to the
    stored string. Every save then showed a phantom change and asked the user
    to confirm it, which is how confirmation dialogs stop being read.
    """
    _cleanup()
    numeric_ish = {
        "vat_registration_number": "4880156723",
        "tax_reference_number": "9130447165",
        "csd_number": "MAAA0774312",
    }
    assert update_company_profile(TEST_COMPANY, numeric_ish, confirmed=True)["written"] is True

    stored = get_company_profile(TEST_COMPANY)
    for key, value in numeric_ish.items():
        assert stored[key] == value, f"{key} round-tripped as {stored[key]!r}"

    again = update_company_profile(TEST_COMPANY, numeric_ish, confirmed=True)
    assert again["status"] == "success"
    assert again["written"] is False, "identical re-save should write nothing"
    assert again["updated_fields"] == []

    preview = cts.preview_questionnaire_save(TEST_COMPANY, dict(VALID_ANSWERS))
    vat_changes = [c for c in preview["changes"] if c["field"] == "vat_registration_number"]
    assert vat_changes == [], f"phantom change reported: {vat_changes}"
    _cleanup()


def test_directors_json_column_still_round_trips():
    """The JSON column is the one that legitimately needs decoding in the diff."""
    _cleanup()
    update_company_profile(TEST_COMPANY, dict(VALID_ANSWERS)["directors"] and
                           {"directors": VALID_ANSWERS["directors"]}, confirmed=True)
    stored = get_company_profile(TEST_COMPANY)
    assert stored["directors"] == VALID_ANSWERS["directors"]
    again = update_company_profile(
        TEST_COMPANY, {"directors": VALID_ANSWERS["directors"]}, confirmed=True
    )
    assert again["written"] is False, "identical directors re-save should write nothing"
    _cleanup()


def test_preview_writes_nothing():
    _cleanup()
    preview = cts.preview_questionnaire_save(TEST_COMPANY, VALID_ANSWERS)
    assert preview["status"] == "ok"
    assert preview["written"] is False
    assert len(preview["changes"]) > 0
    assert get_company_profile(TEST_COMPANY) == {}


# ===========================================================================
# 3. The signature boundary
# ===========================================================================

def test_signature_asset_keys_are_refused():
    for key in ("signature_image", "esignature", "digital_signature", "signature_base64"):
        try:
            assert_no_signature_asset({key: "anything"})
        except SignatureAssetRefused:
            continue
        raise AssertionError(f"{key} was not refused")


def test_signatory_name_refuses_image_payloads():
    bad_values = [
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==",
        "/uploads/naledi_signature.png",
        "C:\\scans\\signature.JPG",
    ]
    for value in bad_values:
        result = update_company_profile(
            TEST_COMPANY, {"authorized_signatory_name": value}, confirmed=True
        )
        assert result["status"] == "refused", f"{value!r} was accepted"
        assert result["reason"] == "signature_asset"
        assert result["written"] is False


def test_signatory_name_accepts_a_name():
    _cleanup()
    result = update_company_profile(
        TEST_COMPANY, {"authorized_signatory_name": "Naledi Molwantwa"}, confirmed=True
    )
    assert result["status"] == "success"
    assert get_company_profile(TEST_COMPANY)["authorized_signatory_name"] == "Naledi Molwantwa"
    _cleanup()


def test_autofill_values_never_carry_a_signature():
    _cleanup()
    cts.save_questionnaire(TEST_COMPANY, VALID_ANSWERS, confirmed=True)
    values = cts.get_autofill_values(TEST_COMPANY)["values"]
    assert "signature" not in values
    assert "signature_date" not in values
    # And the signatory's name is exposed separately, clearly labelled.
    assert "signature" not in qs.CANONICAL_TO_PROFILE_COLUMN
    _cleanup()


# ===========================================================================
# 4. Questionnaire validation
# ===========================================================================

def test_director_state_employee_must_be_explicit():
    answers = dict(VALID_ANSWERS)
    answers["directors"] = [{"name": "Naledi Molwantwa", "id_number": "8203015009089"}]
    result = qs.validate_answers(answers)
    assert result["valid"] is False
    assert "directors[0].is_state_employee" in result["errors"]


def test_director_state_employee_false_is_accepted_when_stated():
    answers = dict(VALID_ANSWERS)
    answers["directors"] = [
        {"name": "Naledi Molwantwa", "id_number": "8203015009089", "is_state_employee": False}
    ]
    result = qs.validate_answers(answers)
    assert result["valid"] is True
    assert result["cleaned"]["directors"][0]["is_state_employee"] is False


def test_sa_identifier_validation():
    cases = [
        ("registration_number", "2019/443221/07", True),
        ("registration_number", "19/443221/7", False),
        ("tax_reference_number", "9130447165", True),
        ("tax_reference_number", "913044716", False),
        ("vat_registration_number", "4880156723", True),
        ("vat_registration_number", "9880156723", False),   # must start with 4
        ("csd_number", "MAAA0774312", True),
        ("csd_number", "0774312", False),
        ("standard_email", "bids@molwantwacivils.co.za", True),
        ("standard_email", "bids@nope", False),
        ("standard_phone", "+27 11 873 4410", True),
        ("standard_phone", "12345", False),
    ]
    for key, value, should_pass in cases:
        answers = dict(VALID_ANSWERS)
        answers[key] = value
        result = qs.validate_answers(answers)
        passed = key not in result["errors"]
        assert passed is should_pass, f"{key}={value!r} expected pass={should_pass}"


def test_sa_id_checksum_catches_a_mistyped_digit():
    ok, err, _ = qs.validate_sa_id_number("8203015009089")
    assert ok is True, err
    # One digit mistyped in the sequence section (0 -> 1). Luhn catches every
    # single-digit error, which is the error people actually make.
    bad, err2, _ = qs.validate_sa_id_number("8203015109089")
    assert bad is False
    assert "checksum" in err2.lower(), err2


def test_sa_id_impossible_date_is_rejected_before_the_checksum():
    """Month 13 is wrong in a way the user can act on; say so specifically."""
    bad, err, _ = qs.validate_sa_id_number("8213015009089")
    assert bad is False
    assert "date of birth" in err.lower(), err


def test_passport_number_is_accepted_with_a_warning():
    ok, err, warn = qs.validate_sa_id_number("ZA4471902")
    assert ok is True
    assert err is None
    assert warn is not None


def test_existing_descriptive_bbbee_value_is_not_rejected():
    """The database already holds 'Level 1 Contributor'. Loading it must work."""
    answers = dict(VALID_ANSWERS)
    answers["bbbee_level"] = "Level 1 Contributor"
    result = qs.validate_answers(answers)
    assert "bbbee_level" not in result["errors"]


# ===========================================================================
# 5. Single source of truth
# ===========================================================================

def _imported_modules(module) -> set:
    """Top-level module names a module actually imports, via AST.

    Deliberately AST rather than a substring grep: these files *discuss*
    sqlite3 in their docstrings, and a grep would match the prose explaining
    why the import is absent.
    """
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _called_names(module) -> set:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                base = f.value
                prefix = base.id + "." if isinstance(base, ast.Name) else ""
                out.add(prefix + f.attr)
    return out


def test_template_store_owns_no_persistence():
    """
    company_template_store must never open a database itself. If this fails, a
    second copy of company facts has been introduced and get_company_profile is
    no longer the single source of truth.
    """
    assert "sqlite3" not in _imported_modules(cts)
    calls = _called_names(cts)
    assert "sqlite3.connect" not in calls
    assert "connect" not in calls
    # Its only persistence dependency is the canonical store.
    assert "agent" in _imported_modules(cts)


def test_questionnaire_schema_does_no_io():
    """The schema module is pure data + pure validation."""
    imports = _imported_modules(qs)
    assert imports <= {"__future__", "re", "dataclasses", "typing"}, imports
    calls = _called_names(qs)
    for forbidden in ("open", "sqlite3.connect", "requests.get", "requests.post"):
        assert forbidden not in calls, f"questionnaire_schema performs I/O: {forbidden}"


def test_end_to_end_questionnaire_round_trip_via_tool_path():
    """
    Fill the questionnaire, save it, and read it back through the tool the
    agent actually calls -- not through a helper written for the test.
    """
    _cleanup()
    validation = qs.validate_answers(VALID_ANSWERS)
    assert validation["valid"] is True, validation["errors"]

    saved = cts.save_questionnaire(TEST_COMPANY, VALID_ANSWERS, confirmed=True)
    assert saved["status"] == "success" and saved["written"] is True

    retrieved, is_error = execute_tool(
        "get_company_profile", {"company_id": TEST_COMPANY}, TEST_COMPANY
    )
    assert is_error is False
    assert retrieved["company_name"] == VALID_ANSWERS["company_name"]
    assert retrieved["tax_reference_number"] == VALID_ANSWERS["tax_reference_number"]
    assert retrieved["authorized_signatory_name"] == VALID_ANSWERS["authorized_signatory_name"]
    # directors round-trips as a real list, not a JSON string.
    assert isinstance(retrieved["directors"], list)
    assert retrieved["directors"][1]["is_state_employee"] is True
    _cleanup()


def test_tenant_pinning_still_holds_for_the_new_fields():
    """A model-supplied company_id is discarded, as tool_dispatch documents."""
    _cleanup()
    cts.save_questionnaire(TEST_COMPANY, VALID_ANSWERS, confirmed=True)
    retrieved, _ = execute_tool(
        "get_company_profile", {"company_id": "pro_corp"}, TEST_COMPANY
    )
    assert retrieved["company_id"] == TEST_COMPANY
    _cleanup()


def test_cleanup_leaves_no_row():
    _cleanup()
    assert get_company_profile(TEST_COMPANY) == {}


# ===========================================================================

def _run_standalone():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:
            failures.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    _cleanup()
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(_run_standalone())
    finally:
        shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)
