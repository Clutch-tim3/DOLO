"""
The regional router never returns a win probability.

`predict_tender_region` used to load the ZA CatBoost model, never call it, and
return `prob = 0.785` as `win_probability` alongside `model_auc = 0.857833`. ZA
is the default region, so every South African tender got the same constant, and
it read as a model output because it came back from a function with a model
loaded in it.

Sibling of `test_price_search_never_fabricates.py` — same failure, same rule.
The routing itself is real and is asserted here too, so that removing the
fabrication cannot quietly remove the signal with it.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from predict.regional_router import detect_region, predict_tender_region

# Literals, not imported constants: this module must be able to run against the
# version of the router that had the bug, or it does not prove anything.
ZA, UK = "ZA", "UK"

ROUTER_SOURCE = Path(__file__).resolve().parent.parent / "predict" / "regional_router.py"

#: Every shape the constant reached the user through.
FORBIDDEN_KEYS = ("win_probability", "model_auc", "probability", "auc")

CONTEXTS = [
    ({"currency": "ZAR"}, "Supply and delivery of trucks under PPPFA rules"),
    ({"currency": "GBP"}, "NHS Trust Legionella Control Services £400,000"),
    ({}, "BBBEE sworn affidavit required"),
    ({}, "Contracts Finder notice, United Kingdom"),
    ({}, ""),
    ({}, "anything at all"),
]


@pytest.mark.parametrize("features,text", CONTEXTS)
def test_no_probability_is_returned(features, text):
    out = predict_tender_region(features, text_context=text)
    for key in FORBIDDEN_KEYS:
        assert key not in out, f"router returned {key!r} for {text!r}"


@pytest.mark.parametrize("features,text", CONTEXTS)
def test_the_old_constants_appear_nowhere_in_the_result(features, text):
    """0.785 was the probability; 0.857833 and 0.694060 were the AUCs."""
    rendered = repr(predict_tender_region(features, text_context=text))
    for constant in ("0.785", "0.857833", "0.85783", "0.694060", "0.69406"):
        assert constant not in rendered, f"{constant} still reaches the caller"


def test_no_hardcoded_probability_remains_in_the_source():
    """
    The bug was a literal, so the regression guard is against literals. A float
    in [0, 1] assigned to anything probability-shaped is the pattern that
    produced 0.785 in the first place.
    """
    source = ROUTER_SOURCE.read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]  # skip the module docstring, which cites them
    offenders = re.findall(r"(?:prob|probability|auc)\w*\s*=\s*0?\.\d+", body, re.IGNORECASE)
    assert not offenders, f"hardcoded probability/AUC assignment: {offenders}"


def test_the_model_is_no_longer_loaded_and_discarded():
    """Loading a model without calling it is what made the constant look real."""
    body = ROUTER_SOURCE.read_text(encoding="utf-8").split('"""', 2)[-1]
    assert "load_model" not in body, "a model is loaded here but no longer used"


# --- the signal that must survive the removal -------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Supply under PPPFA rules", ZA),
    ("BBBEE sworn affidavit required", ZA),
    ("Contracts Finder notice", UK),
    ("United Kingdom procurement", UK),
    ("", ZA),  # documented default for eTenders
])
def test_region_detection_still_works(text, expected):
    assert detect_region(text) == expected
    assert predict_tender_region({}, text_context=text)["region"] == expected


def test_currency_still_routes():
    assert predict_tender_region({"currency": "GBP"})["region"] == UK
    assert predict_tender_region({"currency": "ZAR"})["region"] == ZA


def test_the_compliance_framework_is_still_returned():
    """This is the real output — which statute the tender is evaluated under."""
    assert "PPPFA" in predict_tender_region({}, text_context="PPPFA")["compliance_framework"]
    assert "MEAT" in predict_tender_region({"currency": "GBP"})["compliance_framework"]


def test_override_is_honoured():
    assert predict_tender_region({}, text_context="PPPFA", override_region=UK)["region"] == UK
