"""
Does the signature blocklist GENERALISE, or is it fitted to the labels already
tested?

This was run as an independent check against phrasings deliberately chosen to
appear nowhere in tests/test_agent_autofill_safety.py. The first result: 26 of
27 passed the blocklist. None of them filled — no alias maps them, and
`decide()` refuses anything off-whitelist — but that made the whitelist the
only thing between a plausible new alias and a pre-filled signature block.

That is the same shape as the SBD 4 trap recorded in BUILD_STATE.md: those
cells survived only because no alias mapped "Full Name". Defence that depends
on a gap staying open is not defence.

South African bid forms are routinely bilingual, and the list carried exactly
one Afrikaans token ("handtekening") and no isiZulu at all.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_autofill.extraction.field_alias_dictionary import match_label
from agent_autofill.fill_engine.never_fill_fields import BlockReason, is_blocked
from agent_autofill.fill_engine.safe_fill_fields import decide

PROFILE = {
    "company_name": "CairoAI (Pty) Ltd",
    "registration_number": "2026/250499/07",
    "csd_number": "MAAA1234567",
    "tax_reference_number": "9012345678",
    "vat_registration_number": "4480290011",
    "physical_address": "Centurion, Gauteng",
    "postal_address": "PO Box 1, Centurion",
    "standard_contact_person": "T. Molwantwa",
    "standard_phone": "+27 12 000 0000",
    "standard_email": "bids@cairoai.co.za",
}

# None of these appear in the original adversarial set.
UNCOVERED_SIGNATURE_LABELS = [
    # Afrikaans
    "Paraaf", "Paraaf van bieder", "Merk hier", "Onderteken deur",
    "Naam van getuie", "Getuie 1",
    # isiZulu
    "Sayina lapha", "Ukusayina", "Igama lofakazi",
    # English conventions the list did not cover
    "For and on behalf of", "Per:", "p.p.", "Authorised representative",
    "Attested by", "Sworn before me", "Thus done and subscribed",
    "In the presence of", "Executed at",
    # A stamp box is a signature by another name
    "Company stamp", "Affix official stamp here", "Thumb print",
    "Mark of the bidder",
    # The drawn line itself, with no word on it
    "X_______________________", "_______________________",
    # An initialling box, abbreviated
    "Par.",
]


@pytest.mark.parametrize("label", UNCOVERED_SIGNATURE_LABELS)
def test_uncovered_signature_labels_are_blocked(label):
    d = is_blocked(label)
    assert d.blocked, f"UNBLOCKED SIGNATURE LABEL: {label!r}"
    assert d.reason is BlockReason.SIGNATURE, f"{label!r} blocked but as {d.reason}"


@pytest.mark.parametrize("label", UNCOVERED_SIGNATURE_LABELS)
def test_uncovered_signature_labels_never_fill(label):
    """
    Belt and braces. Even with a whitelisted canonical field forced on and a
    perfect match score, nothing is written.
    """
    dec = decide("company_name", label, PROFILE, match_score=100.0)
    assert dec.fill is False
    assert dec.value is None


@pytest.mark.parametrize("label", UNCOVERED_SIGNATURE_LABELS)
def test_uncovered_signature_labels_do_not_fill_through_the_real_pipeline(label):
    """
    The realistic route: alias match, then decide(). This passed before the
    blocklist was extended — via the whitelist alone — and must keep passing
    for the stronger reason now.
    """
    m = match_label(label)
    if not m:
        return  # no alias, so nothing to decide
    canonical = m[0] if isinstance(m, tuple) else getattr(m, "canonical_field", None)
    score = m[1] if isinstance(m, tuple) else getattr(m, "score", 0.0)
    if not canonical:
        return
    assert decide(canonical, label, PROFILE, match_score=score).fill is False


# --- the other half: this must not become a wall --------------------------

LEGITIMATE = [
    # Deliberately exempted in BUILD_STATE.md — do not re-break these.
    "CAPACITY UNDER WHICH THIS BID IS SIGNED",
    "TOTAL NUMBER OF ITEMS OFFERED",
    # Ordinary safe fields.
    "NAME OF BIDDER", "Company Registration Number", "Physical Address",
    "Postal Address", "E-mail address", "CSD Number",
    "VAT Registration Number", "Tax Reference Number", "Contact person",
    # Near-misses for the new patterns: "mark", "stamp", "par" as substrings.
    "Trade mark registration number", "Date stamp received",
    "Paragraph 3 reference number",
]


@pytest.mark.parametrize("label", LEGITIMATE)
def test_legitimate_labels_are_not_over_blocked(label):
    d = is_blocked(label)
    assert not d.blocked, f"OVER-BLOCKED: {label!r} as {d.reason}"


def test_capacity_of_signatory_stays_exempt_and_that_is_deliberate():
    """
    'Capacity of signatory' and 'CAPACITY UNDER WHICH THIS BID IS SIGNED' are
    the same field asked two ways. The capacity exemption clears both, which is
    coherent — blocking one and not the other would not be.

    An earlier version of this test justified that with "capacity is not on the
    fill whitelist". That was wrong: `capacity` IS whitelisted, mapping to the
    `authorized_signatory_capacity` profile column. It returned False only
    because the profile above has no such key. The real justification is that a
    capacity is a factual role — "Director" — not a signature, and the form asks
    for it in plain text. Filling it is intended.
    """
    assert is_blocked("Capacity of signatory").blocked is False
    assert is_blocked("CAPACITY UNDER WHICH THIS BID IS SIGNED").blocked is False

    # Absent from this profile, so nothing is invented.
    assert decide("capacity", "Capacity of signatory", PROFILE, 100.0).fill is False

    # Present, so it fills — both phrasings, identically.
    with_capacity = dict(PROFILE, authorized_signatory_capacity="Director")
    for label in ("Capacity of signatory",
                  "CAPACITY UNDER WHICH THIS BID IS SIGNED"):
        dec = decide("capacity", label, with_capacity, 100.0)
        assert dec.fill is True, f"{label!r}: {dec.reason}"
        assert dec.value == "Director"
