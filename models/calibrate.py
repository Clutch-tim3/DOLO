import sys
import json
import pickle
from pathlib import Path
import numpy as np
import pandas as pd
import xgboost as xgb
import lightgbm as lgb
from catboost import CatBoostClassifier
from sklearn.isotonic import IsotonicRegression

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_XGB_PATH = PROJECT_ROOT / "models" / "saved" / "model_xgb_conquest.json"
MODEL_LGB_PATH = PROJECT_ROOT / "models" / "saved" / "model_lgb_conquest.txt"
MODEL_CB_PATH = PROJECT_ROOT / "models" / "saved" / "model_cb_conquest.cbm"
WEIGHTS_PATH = PROJECT_ROOT / "models" / "saved" / "ensemble_weights.json"
X_VAL_PATH = PROJECT_ROOT / "data" / "splits" / "X_val.parquet"
Y_VAL_PATH = PROJECT_ROOT / "data" / "splits" / "y_val.parquet"
CALIBRATOR_PATH = PROJECT_ROOT / "models" / "saved" / "calibrator_conquest.pkl"

def main():
    print("Loading validation data for calibration...")
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

    print("Generating validation ensemble predictions...")
    dval = xgb.DMatrix(X_val)
    xgb_probs = xgb_model.predict(dval)
    lgb_probs = lgb_model.predict(X_val)
    cb_probs = cb_model.predict_proba(X_val)[:, 1]
    
    val_probs = (w_xgb * xgb_probs) + (w_lgb * lgb_probs) + (w_cb * cb_probs)
    
    print("Fitting Isotonic Regression...")
    calibrator = IsotonicRegression(out_of_bounds='clip')
    calibrator.fit(val_probs, y_val)
    
    print("Saving calibrator...")
    with open(CALIBRATOR_PATH, 'wb') as f:
        pickle.dump(calibrator, f)
        
    print(f"Calibrator saved to {CALIBRATOR_PATH}")

if __name__ == "__main__":
    main()
