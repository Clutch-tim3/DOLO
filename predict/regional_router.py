#!/usr/bin/env python3
"""
regional_router.py — which procurement regime a tender falls under.

WHAT THIS RETURNS, AND WHAT IT DELIBERATELY DOES NOT
----------------------------------------------------
This module routes a tender to a regime: South Africa (PPPFA 80/20 or 90/10)
or the United Kingdom (MEAT, PCR 2015). That routing is real — it is read from
currency and document text, and it decides which compliance rules apply.

It does **not** return a win probability, and it does not return an AUC.

It used to return both. The ZA branch loaded the CatBoost model and then never
called it:

    model.load_model(str(ZA_MODEL_PATH))
    prob = 0.785 # Calibrated probability for ZA test

ZA is the default region, so every South African tender — the whole product —
was answered with the constant 0.785 presented as a win probability. The
alongside `model_auc` of 0.857833 came from `metrics_conquest_za.json`, whose
`auc_val` and `auc_test` are the identical number on 1,079 rows, which is what
a file with no held-out split looks like.

Probabilities have exactly one home: `predict.predict.predict()`, whose
held-out performance is stated by `predict.model_validation` and travels with
the number. A second source that invents one is how the constant survived — it
looked like a model result because it was returned from a function that had a
model loaded in it.

The model loads were removed with the constant. A load whose result is
discarded is not a safety net; it is the disguise.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ZA = "ZA"
UK = "UK"

#: The regime each region is evaluated under. Both are statute, not estimates.
FRAMEWORKS = {
    ZA: "PPPFA 80/20 & 90/10",
    UK: "MEAT PCR 2015",
}

ENGINES = {
    ZA: "Conquest-ZA (South Africa PPPFA)",
    UK: "Conquest-UK (United Kingdom MEAT)",
}

RECOMMENDATIONS = {
    ZA: [
        "Verify Tax Clearance Pin",
        "Attach BBBEE Sworn Affidavit / SANAS Certificate",
    ],
    UK: [
        "Ensure CPV code alignment",
        "Verify social value statement compliance",
    ],
}


def detect_region(text: str = "", currency: str = "") -> str:
    """Which procurement regime a tender falls under, from its own text."""
    text_upper = text.upper()
    curr_upper = currency.upper()

    if ("GBP" in curr_upper or "£" in text
            or "CONTRACTS FINDER" in text_upper
            or "UNITED KINGDOM" in text_upper):
        return UK
    if ("ZAR" in curr_upper or "RANDS" in text_upper
            or "BBBEE" in text_upper or "PPPFA" in text_upper
            or "SOUTH AFRICA" in text_upper):
        return ZA

    # Default to ZA for eTenders compatibility.
    return ZA


def predict_tender_region(features_dict: dict = None,
                          text_context: str = "",
                          override_region: str = None) -> dict:
    """
    The regime a tender is evaluated under, and what that regime requires.

    Carries no probability. See the module docstring: the caller that wants a
    win probability calls the prediction pipeline, which states what its number
    is worth. `features_dict` is accepted for the currency hint only.
    """
    features_dict = features_dict or {}
    region = (override_region if override_region in (ZA, UK)
              else detect_region(text_context, features_dict.get("currency", "")))

    return {
        "region": region,
        "engine": ENGINES[region],
        "compliance_framework": FRAMEWORKS[region],
        "recommendations": list(RECOMMENDATIONS[region]),
    }


if __name__ == "__main__":
    print("=== Regional Auto-Router Diagnostic Test ===")
    print("\nSA Tender Routing Result:", json.dumps(predict_tender_region(
        {"currency": "ZAR"},
        text_context="Supply and delivery of trucks under PPPFA rules",
    ), indent=2))
    print("\nUK Tender Routing Result:", json.dumps(predict_tender_region(
        {"currency": "GBP"},
        text_context="NHS Trust Legionella Control Services £400,000",
    ), indent=2))
