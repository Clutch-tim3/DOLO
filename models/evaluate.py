#!/usr/bin/env python3
"""
evaluate.py — Full model evaluation and diagnostics with plots.

Loads the trained Ensemble (XGB, LGBM, CB), fits IsotonicRegression on validation set,
generates diagnostic plots on test set, prints classification reports, and finds
optimal F1-maximising threshold. Saves the calibrator and threshold.
"""

import json
import sys
import pickle
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import xgboost as xgb
import lightgbm as lgb
from catboost import CatBoostClassifier
from sklearn.calibration import calibration_curve
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import (
    auc, classification_report, confusion_matrix, f1_score, precision_recall_curve, roc_curve
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPLITS_DIR = PROJECT_ROOT / "data" / "splits"
MODEL_DIR = PROJECT_ROOT / "models" / "saved"
PLOTS_DIR = PROJECT_ROOT / "models" / "plots"
THRESHOLD_PATH = PROJECT_ROOT / "models" / "threshold.json"
CALIBRATOR_PATH = PROJECT_ROOT / "models" / "saved" / "calibrator_v1.pkl"

PLOTS_DIR.mkdir(parents=True, exist_ok=True)

def load_split(name: str):
    X = pd.read_parquet(SPLITS_DIR / f"X_{name}.parquet")
    y = pd.read_parquet(SPLITS_DIR / f"y_{name}.parquet")["did_win"].values.astype(int)
    id_cols = [c for c in X.columns if c in ("tender_id", "lot_id", "bid_id", "persistent_id", "bidder_masterid", "buyer_masterid", "bidder_name", "buyer_name")]
    if id_cols: X = X.drop(columns=id_cols)
    return X, y

def predict_ensemble(X, xgb_model, lgb_model, cb_model):
    dmat = xgb.DMatrix(X, feature_names=list(X.columns))
    p_xgb = xgb_model.predict(dmat)
    p_lgb = lgb_model.predict(X)
    p_cb = cb_model.predict_proba(X)[:, 1]
    return (p_xgb + p_lgb + p_cb) / 3.0

def plot_roc_curve(y_true, y_prob, save_path):
    fpr, tpr, _ = roc_curve(y_true, y_prob)
    roc_auc = auc(fpr, tpr)
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.plot(fpr, tpr, color="#2196F3", lw=2, label=f"ROC curve (AUC = {roc_auc:.4f})")
    ax.plot([0, 1], [0, 1], color="grey", lw=1, linestyle="--")
    ax.set(xlabel="False Positive Rate", ylabel="True Positive Rate", title="ROC Curve", xlim=[0,1], ylim=[0,1.05])
    ax.legend(loc="lower right")
    fig.tight_layout(); fig.savefig(save_path, dpi=150); plt.close(fig)
    return roc_auc

def plot_precision_recall_curve(y_true, y_prob, save_path):
    precision, recall, _ = precision_recall_curve(y_true, y_prob)
    pr_auc = auc(recall, precision)
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.plot(recall, precision, color="#4CAF50", lw=2, label=f"PR curve (AUC = {pr_auc:.4f})")
    ax.axhline(y=y_true.mean(), color="grey", lw=1, linestyle="--", label=f"Baseline ({y_true.mean():.3f})")
    ax.set(xlabel="Recall", ylabel="Precision", title="PR Curve", xlim=[0,1], ylim=[0,1.05])
    ax.legend(loc="upper right")
    fig.tight_layout(); fig.savefig(save_path, dpi=150); plt.close(fig)
    return pr_auc

def plot_calibration_curve(y_true, y_prob, save_path):
    prob_true, prob_pred = calibration_curve(y_true, y_prob, n_bins=10, strategy="uniform")
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.plot(prob_pred, prob_true, marker="o", color="#FF9800", lw=2, label="Model")
    ax.plot([0, 1], [0, 1], color="grey", lw=1, linestyle="--")
    ax.set(xlabel="Mean Predicted Probability", ylabel="Actual Win Rate", title="Calibration Curve", xlim=[0,1], ylim=[0,1.05])
    ax.legend(loc="upper left")
    fig.tight_layout(); fig.savefig(save_path, dpi=150); plt.close(fig)

def main():
    print("=" * 70, flush=True)
    print("  ENSEMBLE EVALUATION & CALIBRATION", flush=True)
    print("=" * 70, flush=True)

    print("\nLoading models...", flush=True)
    xgb_model = xgb.Booster()
    xgb_model.load_model(str(MODEL_DIR / "model_xgb_v1.json"))
    lgb_model = lgb.Booster(model_file=str(MODEL_DIR / "model_lgb_v1.txt"))
    cb_model = CatBoostClassifier().load_model(str(MODEL_DIR / "model_cb_v1.cbm"))
    
    print("\nLoading splits...", flush=True)
    X_val, y_val = load_split("val")
    X_test, y_test = load_split("test")

    print("\nGenerating uncalibrated ensemble predictions...", flush=True)
    y_prob_val_uncal = predict_ensemble(X_val, xgb_model, lgb_model, cb_model)
    y_prob_test_uncal = predict_ensemble(X_test, xgb_model, lgb_model, cb_model)

    print("\nFitting Isotonic Regression on Validation set...", flush=True)
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(y_prob_val_uncal, y_val)
    
    with open(CALIBRATOR_PATH, "wb") as f:
        pickle.dump(calibrator, f)
    print(f"  → Calibrator saved to: {CALIBRATOR_PATH}")

    print("\nCalibrating predictions...", flush=True)
    y_prob_val = calibrator.transform(y_prob_val_uncal)
    y_prob_test = calibrator.transform(y_prob_test_uncal)

    print("\nGenerating plots...", flush=True)
    roc_auc = plot_roc_curve(y_test, y_prob_test, PLOTS_DIR / "roc_curve.png")
    pr_auc = plot_precision_recall_curve(y_test, y_prob_test, PLOTS_DIR / "pr_curve.png")
    plot_calibration_curve(y_test, y_prob_test, PLOTS_DIR / "calibration_curve.png")

    # Optimal threshold search on CALIBRATED val set
    thresholds = np.arange(0.10, 0.91, 0.01)
    best_f1, best_thr = -1.0, 0.5
    for thr in thresholds:
        f1 = f1_score(y_val, (y_prob_val >= thr).astype(int), zero_division=0)
        if f1 > best_f1:
            best_f1 = f1
            best_thr = float(round(thr, 2))

    y_pred_opt = (y_prob_test >= best_thr).astype(int)
    test_f1_opt = f1_score(y_test, y_pred_opt, zero_division=0)

    print(f"\n  Optimal Calibrated Threshold: {best_thr:.2f}")
    print(f"  Best F1 on val: {best_f1:.4f}")
    print(f"  Test F1 at optimal: {test_f1_opt:.4f}")
    
    print(classification_report(y_test, y_pred_opt, target_names=["Lose (0)", "Win (1)"], digits=4))

    threshold_data = {
        "optimal_threshold": best_thr,
        "val_f1": round(best_f1, 4),
        "test_f1_at_optimal": round(test_f1_opt, 4),
        "roc_auc_test": round(roc_auc, 4),
        "pr_auc_test": round(pr_auc, 4),
    }
    with open(THRESHOLD_PATH, "w") as f:
        json.dump(threshold_data, f, indent=2)

    print("\n" + "=" * 70)
    print("  EVALUATION COMPLETE")
    print("=" * 70)

if __name__ == "__main__":
    main()
