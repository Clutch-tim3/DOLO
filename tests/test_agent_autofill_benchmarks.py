"""
The fill benchmarks recorded in BUILD_STATE.md, locked so they cannot drift.

Every other autofill test asks "can this be abused?". These ask the opposite
and equally necessary question: does it still fill a form correctly? A
blocklist change that quietly stopped filling anything would pass the whole
adversarial suite.

Both numbers are reproduced from BUILD_STATE.md:
  MBD 1 generated fixture — 14 of 18, with signature, date, price and method
                            statement blocked
  SBD 4 Annexure A (real) — 0 of 43, every cell marked

The MBD 1 profile must use the real profile column names, not the canonical
field names. They differ, and guessing them produces 10/18 and the false
impression of a regression: `cell_phone_number` reads `standard_cell`,
`fax_number` reads `standard_fax`, `capacity` reads
`authorized_signatory_capacity`.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("AGENT_DB_DIR", tempfile.mkdtemp(prefix="cairo-bench-db-"))
os.environ.setdefault("AGENT_GENERATED_DIR", tempfile.mkdtemp(prefix="cairo-bench-gen-"))
os.environ.setdefault("AUTOFILL_STAMP_SECRET", "test-only-not-a-real-secret")

from agent_autofill.extraction.field_alias_dictionary import match_label   # noqa: E402
from agent_autofill.fill_engine.document_filler import fill_docx           # noqa: E402
from agent_autofill.fill_engine.safe_fill_fields import SAFE_FILL_FIELDS   # noqa: E402

MBD1 = ROOT / "tests" / "fixtures" / "sa_forms_generated" / "mbd1_supplier_info.docx"
SBD4 = ROOT / "tests" / "fixtures" / "sa_forms" / "REVISED SBD 4 -Annexure A.docx"

FULL_PROFILE = {
    "company_name": "CairoAI (Pty) Ltd",
    "registration_number": "2026/250499/07",
    "csd_number": "MAAA1234567",
    "bbbee_level": "Level 1 Contributor",
    "tax_reference_number": "9012345678",
    "vat_registration_number": "4480290011",
    "physical_address": "Centurion, Gauteng",
    "postal_address": "PO Box 1, Centurion",
    "standard_contact_person": "T. Molwantwa",
    "standard_phone": "+27 12 000 0000",
    "standard_email": "bids@cairoai.co.za",
    SAFE_FILL_FIELDS["cell_phone_number"]: "+27 82 000 0000",
    SAFE_FILL_FIELDS["fax_number"]: "+27 12 000 0001",
    SAFE_FILL_FIELDS["tax_compliance_pin"]: "TCS0001234567",
    SAFE_FILL_FIELDS["capacity"]: "Director",
}


@pytest.mark.skipif(not MBD1.exists(), reason="MBD 1 fixture missing")
def test_mbd1_benchmark_is_14_of_18(tmp_path):
    result = fill_docx(MBD1, tmp_path / "mbd1.docx", FULL_PROFILE, match_label)
    total = len(result.filled) + len(result.skipped)
    assert (len(result.filled), total) == (14, 18), (
        f"MBD 1 benchmark moved: {len(result.filled)}/{total}, expected 14/18"
    )
    assert {str(s.label).upper() for s in result.skipped} == {
        "SIGNATURE OF BIDDER", "DATE", "TOTAL BID PRICE", "METHOD STATEMENT",
    }


@pytest.mark.skipif(not SBD4.exists(), reason="SBD 4 fixture missing")
def test_sbd4_benchmark_is_0_of_43(tmp_path):
    """
    Every cell refused as declaration context — not because no alias happens to
    match, which is the trap BUILD_STATE.md records.
    """
    result = fill_docx(SBD4, tmp_path / "sbd4.docx", FULL_PROFILE, match_label)
    total = len(result.filled) + len(result.skipped)
    assert (len(result.filled), total) == (0, 43), (
        f"SBD 4 benchmark moved: {len(result.filled)}/{total}, expected 0/43"
    )
    reasons = {str(s.reason) for s in result.skipped}
    assert all("eclaration" in r for r in reasons), reasons
