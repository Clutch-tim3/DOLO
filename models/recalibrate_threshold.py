import sys
import json
import datetime
import pickle
from pathlib import Path
import numpy as np
import pandas as pd
import xgboost as xgb
import lightgbm as lgb
from catboost import CatBoostClassifier
from sklearn.metrics import precision_recall_curve, f1_score

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_XGB_PATH = PROJECT_ROOT / "models" / "saved" / "model_xgb_v1.json"
MODEL_LGB_PATH = PROJECT_ROOT / "models" / "saved" / "model_lgb_v1.txt"
MODEL_CB_PATH = PROJECT_ROOT / "models" / "saved" / "model_cb_v1.cbm"
WEIGHTS_PATH = PROJECT_ROOT / "models" / "saved" / "ensemble_weights.json"
CALIBRATOR_PATH = PROJECT_ROOT / "models" / "saved" / "calibrator_v1.pkl"
THRESHOLD_PATH = PROJECT_ROOT / "models" / "threshold.json"
X_VAL_PATH = PROJECT_ROOT / "data" / "splits" / "X_val.parquet"
Y_VAL_PATH = PROJECT_ROOT / "data" / "splits" / "y_val.parquet"

def main():
    print("Loading validation data...")
    X_val = pd.read_parquet(X_VAL_PATH)
    y_val = pd.read_parquet(Y_VAL_PATH)['did_win'].values

    print("Loading models...")
    xgb_model = xgb.Booster()
    xgb_model.load_model(str(MODEL_XGB_PATH))
    
    lgb_model = lgb.Booster(model_file=str(MODEL_LGB_PATH))
    cb_model = CatBoostClassifier().load_model(str(MODEL_CB_PATH))
    
    if WEIGHTS_PATH.exists():
        with open(WEIGHTS_PATH) as f:
            weights = json.load(f)
            w_xgb = weights.get('xgb', 0.3333)
            w_lgb = weights.get('lgb', 0.3333)
            w_cb = weights.get('cb', 0.3333)
    else:
        w_xgb, w_lgb, w_cb = 1/3, 1/3, 1/3
        with open(WEIGHTS_PATH, 'w') as f:
            json.dump({'xgb': w_xgb, 'lgb': w_lgb, 'cb': w_cb}, f)

    with open(CALIBRATOR_PATH, 'rb') as f:
        calibrator = pickle.load(f)

    print("Generating predictions...")
    dval = xgb.DMatrix(X_val)
    xgb_probs = xgb_model.predict(dval)
    lgb_probs = lgb_model.predict(X_val)
    cb_probs = cb_model.predict_proba(X_val)[:, 1]
    
    val_probs_uncal = (w_xgb * xgb_probs) + (w_lgb * lgb_probs) + (w_cb * cb_probs)
    val_probs = calibrator.transform(val_probs_uncal)
    
    print("Calculating Precision-Recall curve...")
    precisions, recalls, thresholds = precision_recall_curve(y_val, val_probs)
    
    with np.errstate(divide='ignore', invalid='ignore'):
        f1_scores = 2 * (precisions * recalls) / (precisions + recalls)
        f1_scores = np.nan_to_num(f1_scores)
    
    best_idx = np.argmax(f1_scores)
    f1_optimal = thresholds[best_idx] if best_idx < len(thresholds) else thresholds[-1]
    f1_at_optimal = f1_scores[best_idx]
    
    valid_indices = np.where(precisions >= 0.40)[0]
    if len(valid_indices) > 0:
        bus_idx = valid_indices[0]
        if bus_idx >= len(thresholds):
            bus_idx = len(thresholds) - 1
        bus_optimal = thresholds[bus_idx]
    else:
        valid_indices_fallback = np.where(precisions >= 0.35)[0]
        if len(valid_indices_fallback) > 0:
            bus_idx = valid_indices_fallback[0]
            if bus_idx >= len(thresholds):
                bus_idx = len(thresholds) - 1
            bus_optimal = thresholds[bus_idx]
        else:
            bus_idx = np.argmax(precisions)
            if bus_idx >= len(thresholds):
                bus_idx = len(thresholds) - 1
            bus_optimal = thresholds[bus_idx]
            
    precision_at_bus = precisions[bus_idx]
    recall_at_bus = recalls[bus_idx]

    print("\nThreshold Analysis Table:")
    print(f"{'Threshold':>9} | {'Precision':>9} | {'Recall':>9} | {'F1':>9}")
    print("-" * 43)
    
    for t in np.arange(0.10, 0.95, 0.05):
        idx = (np.abs(thresholds - t)).argmin()
        print(f"{t:9.2f} | {precisions[idx]:9.4f} | {recalls[idx]:9.4f} | {f1_scores[idx]:9.4f}")

    print(f"\nF1-optimal threshold: {f1_optimal:.4f} (F1={f1_at_optimal:.4f})")
    print(f"Business-optimal threshold: {bus_optimal:.4f} (precision={precision_at_bus:.4f}, recall={recall_at_bus:.4f})")
    print("Recommended: use business-optimal for real bid decisions\n")

    out_data = {
        "f1_optimal": float(f1_optimal),
        "f1_at_f1_optimal": float(f1_at_optimal),
        "business_optimal": float(bus_optimal),
        "precision_at_business": float(precision_at_bus),
        "recall_at_business": float(recall_at_bus),
        "active_threshold": "business_optimal",
        "threshold_value": float(bus_optimal),
        "updated_at": datetime.datetime.now().isoformat()
    }
    
    with open(THRESHOLD_PATH, 'w') as f:
        json.dump(out_data, f, indent=4)
    print(f"Saved to {THRESHOLD_PATH}")

if __name__ == "__main__":
    main()
