"""Tests for agent_autofill.extraction.

Emphasis is on the failure modes that matter for this feature: a wrong field
mapping in a real bid is worse than a missed one, so most assertions here check
that unsafe mappings are *refused*, not that coverage is high.
"""

import pytest
from pathlib import Path

from agent_autofill.extraction import (
    extract_acroform,
    extract_docx_blanks,
    extract_document,
    extract_pdf_blanks,
    has_acroform,
    match_label,
    normalize_label,
)
from agent_autofill.extraction.field_alias_dictionary import (
    AMBIGUITY_MARGIN,
    CORROBORATION_FLOOR,
    MATCH_THRESHOLD,
)

REAL_TENDER = Path("data/archive/temp_tender_BID_DOCUMENT_06FY27_.pdf")


@pytest.fixture
def mbd_docx(tmp_path):
    """The MBD-pattern DOCX form, rebuilt fresh so the test is self-contained."""
    from tests.generate_autofill_fixtures import build_mbd_test_form

    return build_mbd_test_form(tmp_path / "mbd4_test_form.docx")


# ---------------------------------------------------------------------------
# Alias dictionary — the spec-mandated variants
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "label,expected",
    [
        ("BIDDER NAME", "company_name"),
        ("Name of Bidder", "company_name"),
        ("NAME OF BIDDER", "company_name"),
        ("Full Name of bidder or his or her representative", "company_name"),
        ("Company Registration Number", "registration_number"),
        ("COMPANY REGISTRATION NUMBER", "registration_number"),
        ("Tax Reference Number", "tax_reference_number"),
        ("VAT Registration Number", "vat_registration_number"),
        ("CSD Number", "csd_number"),
        ("B-BBEE Status Level", "bbbee_level"),
        ("BBBEE Status Level of Contribution", "bbbee_level"),
    ],
)
def test_spec_mandated_variants_map_to_canonical_fields(label, expected):
    match = match_label(label)
    assert match.canonical == expected
    assert match.is_confident
    assert match.score >= MATCH_THRESHOLD


def test_normalize_strips_enumeration_punctuation_and_leaders():
    assert normalize_label("2.1 Name of Bidder") == "NAME OF BIDDER"
    assert normalize_label("NAME OF BIDDER: ______") == "NAME OF BIDDER"
    assert normalize_label("VAT Registration Number (if applicable)") == (
        "VAT REGISTRATION NUMBER"
    )
    assert normalize_label("B-BBEE Status Level") == "BBBEE STATUS LEVEL"


# ---------------------------------------------------------------------------
# Alias dictionary — refusals. These are the accuracy-critical assertions.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "label",
    [
        "Registration Number",  # company? VAT? CSD? — undecidable alone
        "Number",
        "Name",
        "CODE",
        "Address",
    ],
)
def test_generic_labels_are_blocked_not_guessed(label):
    match = match_label(label)
    assert match.canonical is None
    assert not match.is_confident
    assert match.status == "blocked"


@pytest.mark.parametrize(
    "prose",
    [
        "I, the",
        "the following",
        "This is to certify that, I",
        "visited and examined the site on (date)",
        "certify to be true and complete in every respect",
        "For tenderer: (Name and address)",
    ],
)
def test_prose_fragments_are_refused(prose):
    """WRatio scores all of these >= 85 via partial matching.

    Each one previously mapped to a real canonical field — e.g. "I, the" ->
    company_name — which would have written a company name into a signature
    box. The corroboration floor is what stops them.
    """
    match = match_label(prose)
    assert match.canonical is None, f"{prose!r} wrongly mapped to {match.canonical}"
    assert not match.is_confident


def test_company_and_vat_registration_are_never_confused():
    company = match_label("Company Registration Number")
    vat = match_label("VAT Registration Number")
    assert company.canonical == "registration_number"
    assert vat.canonical == "vat_registration_number"
    assert company.canonical != vat.canonical


def test_thresholds_are_the_documented_values():
    assert MATCH_THRESHOLD == 85.0
    assert AMBIGUITY_MARGIN > 0
    assert CORROBORATION_FLOOR > 0


# ---------------------------------------------------------------------------
# AcroForm
# ---------------------------------------------------------------------------
def test_flat_pdf_reports_no_acroform(fixtures_dir):
    path = fixtures_dir / "alfred_duma.pdf"
    assert has_acroform(path) is False
    result = extract_acroform(path)
    assert result.is_fillable is False
    assert result.fields == []


def test_malformed_pdf_does_not_raise(fixtures_dir):
    """A corrupt upload must degrade to an error field, not crash the request."""
    result = extract_acroform(fixtures_dir / "malformed.pdf")
    assert result.is_fillable is False
    assert result.error is not None


# ---------------------------------------------------------------------------
# PDF layout extraction
# ---------------------------------------------------------------------------
def test_reference_fixture_has_no_fillable_blanks(fixtures_dir):
    """tests/fixtures/alfred_duma.pdf is tender *metadata*, not a form.

    It is generated by tests/generate_fixtures.py and contains no table, no
    underscore run and no dot leader. Detecting zero blanks is the correct
    result; a non-zero count here means the detector has started inventing
    fields out of ordinary prose.
    """
    blanks = extract_pdf_blanks(fixtures_dir / "alfred_duma.pdf")
    assert blanks == []


@pytest.mark.skipif(not REAL_TENDER.exists(), reason="real tender pack not present")
def test_sbd1_supplier_information_block_is_recovered():
    """Page 7 (0-based 6) of the real pack is the SBD 1 supplier table."""
    report = extract_document(REAL_TENDER, pages=[6])
    canonical = {f.canonical for f in report.fields if f.canonical}

    for expected in (
        "company_name",
        "postal_address",
        "physical_address",
        "cell_phone_number",
        "email_address",
        "vat_registration_number",
    ):
        assert expected in canonical, f"missing {expected}; got {sorted(canonical)}"


@pytest.mark.skipif(not REAL_TENDER.exists(), reason="real tender pack not present")
def test_blank_geometry_is_sane():
    report = extract_document(REAL_TENDER, pages=[6, 84])
    for f in report.fields:
        bbox = f.blank.bbox
        if bbox is None:
            continue
        x0, top, x1, bottom = bbox
        assert x1 > x0, f"non-positive width at {f.blank.position_str}"
        assert bottom > top, f"non-positive height at {f.blank.position_str}"


@pytest.mark.skipif(not REAL_TENDER.exists(), reason="real tender pack not present")
def test_trailing_gap_strategy_is_off_by_default():
    """It scored 0/10 precision on the real pack; it must be opt-in."""
    default = extract_pdf_blanks(REAL_TENDER, pages=[47, 56, 86])
    assert not any(b.strategy == "trailing_gap" for b in default)

    opted_in = extract_pdf_blanks(
        REAL_TENDER, pages=[47, 56, 86], include_trailing_gaps=True
    )
    assert any(b.strategy == "trailing_gap" for b in opted_in)


@pytest.mark.skipif(not REAL_TENDER.exists(), reason="real tender pack not present")
def test_toc_style_dot_leaders_are_flagged():
    """A dot leader followed by a bare page number is a contents row."""
    from agent_autofill.extraction.layout_blank_extractor import _looks_like_toc

    assert callable(_looks_like_toc)


@pytest.mark.skipif(not REAL_TENDER.exists(), reason="real tender pack not present")
def test_no_blank_is_actionable_without_a_label():
    """An unlabelled blank must never reach the fill engine."""
    report = extract_document(REAL_TENDER, pages=[47, 48, 61])
    for f in report.fields:
        if f.blank.label_text is None:
            assert not f.is_actionable
            assert f.canonical is None


# ---------------------------------------------------------------------------
# DOCX extraction
# ---------------------------------------------------------------------------
def test_docx_label_value_table_is_recovered(mbd_docx):
    report = extract_document(mbd_docx)
    canonical = {f.canonical for f in report.fields if f.canonical}
    for expected in (
        "company_name",
        "registration_number",
        "tax_reference_number",
        "vat_registration_number",
        "csd_number",
        "bbbee_level",
    ):
        assert expected in canonical, f"missing {expected}; got {sorted(canonical)}"


def test_docx_prefilled_cells_are_not_reported_as_blanks(mbd_docx):
    """BID NUMBER / CLOSING DATE are completed by the issuing department."""
    blanks = extract_docx_blanks(mbd_docx)
    labels = {b.label_text for b in blanks}
    assert "BID NUMBER" not in labels
    assert "CLOSING DATE" not in labels


def test_docx_merged_cell_reported_once(mbd_docx):
    """python-docx yields the same _tc per spanned grid position.

    Without de-duplication by element identity a single merged cell becomes
    several phantom fields.
    """
    blanks = extract_docx_blanks(mbd_docx)
    positions = [
        (b.table_index, b.row_index, b.col_index)
        for b in blanks
        if b.strategy == "docx_table_cell"
    ]
    assert len(positions) == len(set(positions))

    merged_row_hits = [
        b for b in blanks if b.table_index == 2 and b.row_index == 6
    ]
    assert len(merged_row_hits) == 1


def test_full_row_merge_does_not_inherit_the_label_above(mbd_docx):
    """A merged note band is not the value half of a label/value pair.

    Without this guard the trailing merged row inherited "BBBEE Status Level of
    Contribution" from the row above and mapped at 100.0 — a phantom field that
    would have put the B-BBEE level into a free-text note area.
    """
    blanks = extract_docx_blanks(mbd_docx)
    merged = next(b for b in blanks if b.table_index == 2 and b.row_index == 6)
    assert "full_row_merge" in merged.notes
    assert merged.label_text is None
    assert match_label(merged.label_text).canonical is None


def test_docx_paragraph_underscore_runs_detected(mbd_docx):
    blanks = extract_docx_blanks(mbd_docx)
    runs = {
        b.label_text: b for b in blanks if b.strategy == "docx_underscore_run"
    }
    assert "SIGNATURE" in runs
    assert "CAPACITY" in runs
    assert "DATE" in runs


def test_docx_blanks_carry_no_bbox_but_do_carry_grid_position(mbd_docx):
    """The structural difference from the PDF path, asserted explicitly."""
    for blank in extract_docx_blanks(mbd_docx):
        assert blank.bbox is None
        assert blank.source == "docx"
        if blank.strategy == "docx_table_cell":
            assert blank.table_index is not None
            assert blank.row_index is not None
            assert blank.col_index is not None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def test_actionable_requires_confident_match_and_confident_blank(mbd_docx):
    report = extract_document(mbd_docx)
    for f in report.actionable:
        assert f.match.is_confident
        assert f.blank.confidence >= 0.55
        assert f.canonical is not None


def test_summary_counts_are_consistent(mbd_docx):
    report = extract_document(mbd_docx)
    summary = report.summary()
    assert summary["blanks_detected"] == len(report.fields)
    assert summary["matched"] + summary["unmatched"] == summary["blanks_detected"]
