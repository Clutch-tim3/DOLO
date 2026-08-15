"""
How much the win probability can be trusted, stated alongside it.

WHY THIS EXISTS
---------------
The win-probability model scores about 0.53-0.56 ROC-AUC on held-out data.
0.50 is a coin flip. Shown on its own, "68% chance of winning" reads as a
measurement; it is closer to a number-shaped guess, and it appears on the same
screen that produces a government bid document.

This is the same failure `agent/quotation/price_search.py` had: a confident
figure with a real-looking provenance and nothing behind it. That was fixed by
returning MANUAL_REVIEW_REQUIRED rather than an invented price. The model
cannot simply return nothing — the ranking is still weakly informative — so
instead every prediction carries what it is worth.

WHY THE MODEL IS WEAK, WHICH IS NOT A TUNING PROBLEM
----------------------------------------------------
The training data has no losing bids in it. `raw_contracts.parquet` is an
awards register: `bid_iswinning` has a single distinct value across all 8,767
rows and `tender_recordedbidscount` is 1 for every one of them. Nobody
recorded who competed and lost.

So `03_build_pairs.py` invents the losers by shuffling bidders between
tenders, and 83% of the training rows are `pair_type='synthetic_negative'`.
The model is trained to tell a real winner from a winner borrowed off another
tender, then asked at inference whether a company will win a tender it is
bidding on. Those are different questions, which is why the answer does not
transfer.

Two things compound it: 29 of 50 features are identical for every bidder
competing for the same tender, so they cannot rank anyone; and 7,088 distinct
bidders share 8,767 awards, so almost nobody recurs and the supplier-history
features that could rank bidders stay empty (`pit_is_incumbent` averages
0.007).

Recovering a 2% sampling bug in the pipeline took training data from 509 rows
to 26,184 and moved validation AUC from 0.500 to 0.531. That ruled out data
volume as the explanation. The signal is not there to find.

WHEN THIS CAN BE REMOVED
------------------------
When the model is trained on real bid outcomes including losses — either bid
submission records that list every tenderer, or outcomes CairoAI collects from
its own users' submitted bids — and held-out AUC is measured, not assumed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("predict.model_validation")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
METRICS_PATH = PROJECT_ROOT / "models" / "metrics_conquest.json"

#: Below this, held-out ranking is too close to chance to call a probability.
#: 0.60 is already generous for a number presented to a user as a percentage.
USABLE_AUC = 0.60

STATUS_NOT_VALIDATED = "NOT_VALIDATED"
STATUS_VALIDATED = "VALIDATED"

_NOTE_NOT_VALIDATED = (
    "This score is not a validated probability. The model is trained on an "
    "awards register that records only winners, so the losing bids it learns "
    "from are synthetic. On held-out data it ranks little better than chance "
    "({auc:.2f} AUC, where 0.50 is a coin flip). Treat it as a rough ordering "
    "at most, and not as a forecast of whether you will win."
)


def _holdout_auc() -> float | None:
    """Test-set ROC-AUC from the last training run, or None if unavailable.

    Read from the metrics file rather than hardcoded, so the number cannot
    drift away from the model actually in use — a stale figure asserting the
    model is fine would be worse than no figure.
    """
    try:
        metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("model_validation: could not read %s: %s", METRICS_PATH, exc)
        return None

    for split in ("test", "val"):
        value = (metrics.get(split) or {}).get("roc_auc")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def validation_status() -> dict:
    """
    What to attach to any response carrying a win probability.

    Fails toward caution: if the metrics file cannot be read, the status is
    NOT_VALIDATED rather than an optimistic default. An unmeasured model is
    not a validated one.
    """
    auc = _holdout_auc()
    if auc is None:
        return {
            "status": STATUS_NOT_VALIDATED,
            "holdout_auc": None,
            "note": ("This score is not a validated probability — the model's "
                     "held-out performance could not be read, so it is treated "
                     "as unmeasured."),
        }

    if auc >= USABLE_AUC:
        return {
            "status": STATUS_VALIDATED,
            "holdout_auc": round(auc, 3),
            "note": (f"Held-out ranking performance is {auc:.2f} AUC "
                     f"(0.50 is chance)."),
        }

    return {
        "status": STATUS_NOT_VALIDATED,
        "holdout_auc": round(auc, 3),
        "note": _NOTE_NOT_VALIDATED.format(auc=auc),
    }


def is_validated() -> bool:
    return validation_status()["status"] == STATUS_VALIDATED
