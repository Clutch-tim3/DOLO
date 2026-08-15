"""
What the win probability is allowed to claim about itself.

The model ranks held-out data at ~0.53-0.56 AUC, where 0.50 is a coin flip,
because it is trained on an awards register with no losing bids in it — the
negatives are synthesised by shuffling bidders between tenders. A bare "68%"
on the screen that produces a government bid document reads as a measurement.

These tests hold the line that `price_search` had to be dragged back to: a
number with nothing behind it must say so.
"""

from __future__ import annotations

import json

import pytest

from predict import model_validation as mv


@pytest.fixture()
def metrics_file(tmp_path, monkeypatch):
    """Point the module at a metrics file this test controls."""
    path = tmp_path / "metrics.json"
    monkeypatch.setattr(mv, "METRICS_PATH", path)
    return path


def _write(path, *, test_auc=None, val_auc=None):
    payload = {}
    if test_auc is not None:
        payload["test"] = {"roc_auc": test_auc}
    if val_auc is not None:
        payload["val"] = {"roc_auc": val_auc}
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_a_near_chance_model_is_not_validated(metrics_file):
    _write(metrics_file, test_auc=0.556)
    status = mv.validation_status()
    assert status["status"] == mv.STATUS_NOT_VALIDATED
    assert mv.is_validated() is False


def test_the_note_says_coin_flip_in_words_a_person_reads(metrics_file):
    _write(metrics_file, test_auc=0.556)
    note = mv.validation_status()["note"].lower()
    assert "not a validated probability" in note
    assert "coin flip" in note
    # And why, not just that — otherwise it reads as boilerplate.
    assert "synthetic" in note


def test_a_genuinely_good_model_would_pass(metrics_file):
    """The gate must be capable of opening, or it is decoration."""
    _write(metrics_file, test_auc=0.82)
    status = mv.validation_status()
    assert status["status"] == mv.STATUS_VALIDATED
    assert status["holdout_auc"] == 0.82
    assert "coin flip" not in status["note"].lower()


def test_the_number_is_read_from_metrics_not_hardcoded(metrics_file):
    """A hardcoded figure would keep asserting a model that no longer exists."""
    _write(metrics_file, test_auc=0.731)
    assert mv.validation_status()["holdout_auc"] == 0.731


def test_test_auc_is_preferred_over_val(metrics_file):
    _write(metrics_file, test_auc=0.55, val_auc=0.91)
    assert mv.validation_status()["holdout_auc"] == 0.55


def test_val_is_used_when_there_is_no_test_figure(metrics_file):
    _write(metrics_file, val_auc=0.54)
    assert mv.validation_status()["holdout_auc"] == 0.54


def test_a_missing_metrics_file_fails_toward_caution(metrics_file):
    """An unmeasured model is not a validated one.

    The tempting default is "assume fine unless proven otherwise", which is
    how an unvalidated model ends up presented as a measurement.
    """
    assert not metrics_file.exists()
    status = mv.validation_status()
    assert status["status"] == mv.STATUS_NOT_VALIDATED
    assert status["holdout_auc"] is None


def test_a_corrupt_metrics_file_fails_toward_caution(metrics_file):
    metrics_file.write_text("{not json", encoding="utf-8")
    assert mv.validation_status()["status"] == mv.STATUS_NOT_VALIDATED


def test_metrics_without_an_auc_fail_toward_caution(metrics_file):
    metrics_file.write_text(json.dumps({"test": {"log_loss": 0.44}}), encoding="utf-8")
    status = mv.validation_status()
    assert status["status"] == mv.STATUS_NOT_VALIDATED
    assert status["holdout_auc"] is None


def test_the_real_shipped_model_is_currently_not_validated():
    """
    Deliberately asserted against the real metrics file, not a fixture.

    If a future training run genuinely clears the bar this fails, and that is
    the point: it forces someone to look at whether the improvement is real
    before the caveat disappears from the product.
    """
    status = mv.validation_status()
    assert status["status"] == mv.STATUS_NOT_VALIDATED, (
        f"held-out AUC is now {status['holdout_auc']} — if that is a real "
        f"improvement on real bid outcomes, update this test deliberately"
    )
