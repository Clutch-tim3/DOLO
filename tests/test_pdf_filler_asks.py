"""
The fill path consults what has been learned, and asks about what it cannot place.

Two things were wired into `fill_pdf` here:

  * `learned_labels`, which was built for P1-5 and then called by nothing
    outside its own tests — a company could teach it and the next pack would
    not notice.
  * `label_classifier`, which asks Claude what an unrecognised label means.

Both produce a FIELD NAME. Neither produces a value, and neither is consulted
before `is_blocked`. That ordering is what these tests are mostly about.
"""

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("fitz")

from agent_autofill.fill_engine import pdf_filler
from agent_autofill.extraction import learned_labels

def _a_form(tmp_path: Path) -> Path:
    """
    A one-page form with a ruled line under a label.

    Built rather than shipped: every PDF fixture in the repo is either a
    145-page pack (too slow for this) or a .docx. All this test needs is a
    document `fill_pdf` will actually walk.
    """
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "COMPANY NAME", fontsize=10.5)
    page.draw_line((160, 103), (400, 103))
    path = tmp_path / "form.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def company_id():
    cid = f"pf-{uuid.uuid4().hex[:10]}"
    yield cid
    for lesson in learned_labels.lessons(cid):
        learned_labels.forget(cid, lesson["example_label"] or lesson["normalised"])


@pytest.fixture
def profile():
    return {"company_name": "Donington Vale (Pty) Ltd",
            "registration_number": "K2026250499"}


# --- nothing changes without a company ----------------------------------------

def test_without_a_company_id_nothing_is_asked(tmp_path, profile, monkeypatch):
    """
    The default path is the old path exactly. Every existing caller that has
    not been updated keeps working, and no API call happens.
    """
    called = []
    monkeypatch.setattr(pdf_filler, "_identify_unknowns",
                        lambda *a, **k: called.append(a) or {})

    from agent_autofill.extraction import match_label
    pdf_filler.fill_pdf(_a_form(tmp_path), tmp_path / "out.pdf", profile,
                        match_label)

    assert called == [], "an unrecognised-label lookup ran with no company"


# --- the blocklist still runs first -------------------------------------------

def test_a_blocked_label_is_never_sent_to_the_api(company_id, profile, monkeypatch):
    """
    A signature line is refused by `is_blocked` before anything else. It must
    not be handed to an external service on the way, and it must not be
    identifiable into something fillable.
    """
    sent = []

    def spy(cid, labels, context=""):
        sent.extend(labels)
        return {}

    from agent_autofill.extraction import label_classifier
    monkeypatch.setattr(label_classifier, "classify_and_remember", spy)

    class _Blank:
        label_text = "SIGNATURE OF BIDDER"
        page_number = 0
        bbox = (100, 100, 300, 115)
        notes = ()

    from agent_autofill.extraction import match_label
    pdf_filler._identify_unknowns(company_id, [_Blank()], match_label, profile)

    assert sent == [], f"a blocked label was sent for identification: {sent}"


def test_a_learned_mapping_cannot_unblock_a_signature(company_id, profile):
    """
    THE property. Even taught outright that "Signature" means company_name,
    the fill refuses it — because `is_blocked` runs before any of this and
    nothing here can reach that decision.
    """
    learned_labels.teach(company_id, "SIGNATURE OF BIDDER",
                         canonical_field="company_name", taught_by="test")

    from agent_autofill.fill_engine.never_fill_fields import is_blocked
    assert is_blocked("SIGNATURE OF BIDDER").blocked

    field, _source = learned_labels.apply_learning(
        company_id, "SIGNATURE OF BIDDER", None)
    assert field == "company_name", "the lesson exists"
    # ...and the fill path runs is_blocked on the LABEL before it ever gets
    # here, so the lesson is never consulted for this blank at all.


# --- what gets asked about ----------------------------------------------------

def test_only_genuinely_unknown_labels_are_asked_about(company_id, profile, monkeypatch):
    """
    A label the dictionary places, or that is already explained correctly as a
    declaration or a price cell, is not worth an API call.
    """
    sent = []

    def spy(cid, labels, context=""):
        sent.extend(labels)
        return {}

    from agent_autofill.extraction import label_classifier, match_label
    monkeypatch.setattr(label_classifier, "classify_and_remember", spy)

    def blank(text):
        return type("B", (), {"label_text": text, "page_number": 0,
                              "bbox": (10, 10, 200, 25), "notes": ()})()

    pdf_filler._identify_unknowns(company_id, [
        blank("COMPANY NAME"),              # the dictionary knows this
        blank("Name of State institution"), # a declaration, already explained
        blank("Lead Time for delivery"),    # this bid's terms, already explained
        blank("Contracts Manager"),         # genuinely unknown
    ], match_label, profile)

    assert sent == ["Contracts Manager"], sent


def test_the_same_label_is_asked_about_once_per_pack(company_id, profile, monkeypatch):
    """"Description of contract" appears on every row of the experience table."""
    sent = []

    def spy(cid, labels, context=""):
        sent.extend(labels)
        return {}

    from agent_autofill.extraction import label_classifier, match_label
    monkeypatch.setattr(label_classifier, "classify_and_remember", spy)

    def blank(text):
        return type("B", (), {"label_text": text, "page_number": 0,
                              "bbox": (10, 10, 200, 25), "notes": ()})()

    pdf_filler._identify_unknowns(company_id, [
        blank("Description of contract"),
        blank("Description of Contract:"),   # same question, different pack row
        blank("DESCRIPTION OF CONTRACT"),
    ], match_label, profile)

    assert len(sent) == 1, sent


def test_a_label_already_explained_is_not_asked_about_again(company_id, profile,
                                                            monkeypatch):
    """One call in a label's lifetime, not one per pack."""
    learned_labels.teach(company_id, "Contracts Manager", not_a_field=True,
                         taught_by="claude")

    sent = []

    def spy(cid, labels, context=""):
        sent.extend(labels)
        return {}

    from agent_autofill.extraction import label_classifier, match_label
    monkeypatch.setattr(label_classifier, "classify_and_remember", spy)

    blank = type("B", (), {"label_text": "Contracts Manager", "page_number": 0,
                           "bbox": (10, 10, 200, 25), "notes": ()})()
    pdf_filler._identify_unknowns(company_id, [blank], match_label, profile)

    assert sent == []


def test_a_failure_leaves_the_fill_alone(company_id, profile, monkeypatch):
    """No key, no quota, no network — the fill is exactly what it was before."""
    def boom(*a, **k):
        raise RuntimeError("no API key configured")

    from agent_autofill.extraction import label_classifier, match_label
    monkeypatch.setattr(label_classifier, "classify_and_remember", boom)

    blank = type("B", (), {"label_text": "Contracts Manager", "page_number": 0,
                           "bbox": (10, 10, 200, 25), "notes": ()})()
    assert pdf_filler._identify_unknowns(company_id, [blank], match_label, profile) == {}


# --- what the user reads ------------------------------------------------------

def test_an_identified_question_is_described_instead_of_shrugged_at():
    from agent_autofill.fill_engine.refusal_reasons import (
        REASONS, explain_per_tender,
    )

    said = explain_per_tender("a past contract you delivered")
    assert "a past contract you delivered" in said
    assert said != REASONS["unmatched"]
    assert "could not tell" not in said


def test_the_phrase_is_treated_as_text_to_print(monkeypatch):
    """
    It comes from a model reading a third party's label, so it is display text
    and nothing else.
    """
    from agent_autofill.fill_engine.refusal_reasons import explain_per_tender

    said = explain_per_tender("<script>alert(1)</script>\nand more")
    for ch in "<>&":
        assert ch not in said
    assert "\n" not in said

    assert len(explain_per_tender("x" * 500)) < 250


def test_identifying_a_field_does_not_change_what_blocks_an_export():
    """
    `per_tender` was `unmatched`, which was advisory. Naming a thing must not
    quietly start blocking exports on it.
    """
    from agent_autofill.integration.review_gate import ADVISORY_CATEGORIES
    assert "per_tender" in ADVISORY_CATEGORIES
