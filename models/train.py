#!/usr/bin/env python3
"""
train.py — Train XGBoost, LightGBM, CatBoost with Optuna hyperparameter search and Ensemble.

Loads time-based splits from data/splits/, runs 150-trial Optuna search for each model,
trains final models with best params, evaluates an ensemble on all splits, and saves
models + metrics.
"""

import json
import sys
import time
import warnings
from pathlib import Path
import gc

import numpy as np
import optuna
import pandas as pd
import xgboost as xgb
import lightgbm as lgb
from catboost import CatBoostClassifier
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
import sqlite3
import pickle

# Suppress verbose logging
optuna.logging.set_verbosity(optuna.logging.WARNING)
warnings.filterwarnings("ignore")

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPLITS_DIR = PROJECT_ROOT / "data" / "splits"
METADATA_PATH = PROJECT_ROOT / "data" / "processed" / "dataset_metadata.json"
MODEL_DIR = PROJECT_ROOT / "models" / "saved"
METRICS_PATH = PROJECT_ROOT / "models" / "metrics_v1.json"
DB_PATH = PROJECT_ROOT / "data" / "procurement.db"

MODEL_DIR.mkdir(parents=True, exist_ok=True)

# ── SQLite helper ──────────────────────────────────────────────────────────
def save_to_sqlite(df, table_name, db_path):
    conn = sqlite3.connect(db_path)
    df.to_sql(table_name, conn, if_exists="replace", index=False)
    conn.close()
    print(f"  → SQLite: {table_name} table saved to {db_path}", flush=True)

# ── Precision@K ────────────────────────────────────────────────────────────
def precision_at_k(y_true, y_pred, group_ids, k=20):
    df = pd.DataFrame({"y_true": y_true, "y_pred": y_pred, "group": group_ids})
    hits = total = 0
    for _, grp in df.groupby("group"):
        if grp["y_true"].sum() == 0:
            continue
        ranked = grp.sort_values("y_pred", ascending=False)
        if ranked.head(k)["y_true"].sum() > 0:
            hits += 1
        total += 1
    return hits / total if total > 0 else 0.0

# ── Calibration across bins ───────────────────────────────────────────────
def calibration_summary(y_true, y_pred, n_bins=10):
    bins = np.linspace(0, 1, n_bins + 1)
    result = []
    for i in range(n_bins):
        mask = (y_pred >= bins[i]) & (y_pred < bins[i + 1])
        if i == n_bins - 1:
            mask = (y_pred >= bins[i]) & (y_pred <= bins[i + 1])
        count = mask.sum()
        if count == 0:
            result.append({"bin": f"{bins[i]:.1f}-{bins[i+1]:.1f}", "count": 0, "mean_predicted": None, "actual_rate": None})
        else:
            result.append({"bin": f"{bins[i]:.1f}-{bins[i+1]:.1f}", "count": int(count), "mean_predicted": float(np.mean(y_pred[mask])), "actual_rate": float(np.mean(y_true[mask]))})
    return result

# ══════════════════════════════════════════════════════════════════════════
def main():
    t0 = time.time()
    print("=" * 70, flush=True)
    print("  Ensemble Training with Optuna (XGBoost, LightGBM, CatBoost)", flush=True)
    print("=" * 70, flush=True)

    # ── 1. Load splits ────────────────────────────────────────────────────
    print("\n[1/8] Loading train/val/test splits...", flush=True)
    split_files = {
        "X_train": SPLITS_DIR / "X_train.parquet",
        "X_val": SPLITS_DIR / "X_val.parquet",
        "X_test": SPLITS_DIR / "X_test.parquet",
        "y_train": SPLITS_DIR / "y_train.parquet",
        "y_val": SPLITS_DIR / "y_val.parquet",
        "y_test": SPLITS_DIR / "y_test.parquet",
    }
    for name, path in split_files.items():
        if not path.exists():
            print(f"  ERROR: {path} not found!", flush=True)
            sys.exit(1)

    X_train = pd.read_parquet(split_files["X_train"])
    X_val = pd.read_parquet(split_files["X_val"])
    X_test = pd.read_parquet(split_files["X_test"])
    y_train = pd.read_parquet(split_files["y_train"]).squeeze()
    y_val = pd.read_parquet(split_files["y_val"]).squeeze()
    y_test = pd.read_parquet(split_files["y_test"]).squeeze()

    # ── 2. Load metadata (scale_pos_weight) ───────────────────────────────
    with open(METADATA_PATH, "r") as f:
        metadata = json.load(f)
    scale_pos_weight = metadata.get("scale_pos_weight", 1.0)
    print(f"  scale_pos_weight = {scale_pos_weight:.4f}", flush=True)

    # ── 3. Handle object columns ──────────────────────────────────────────
    group_col = None
    for candidate in ["tender_id", "lot_id"]:
        if candidate in X_train.columns:
            group_col = candidate
            break

    group_train = X_train[group_col].values if group_col else None
    group_val = X_val[group_col].values if group_col else None
    group_test = X_test[group_col].values if group_col else None

    obj_cols = X_train.select_dtypes(include=["object", "category"]).columns.tolist()
    if obj_cols:
        X_train.drop(columns=obj_cols, inplace=True)
        X_val.drop(columns=obj_cols, inplace=True)
        X_test.drop(columns=obj_cols, inplace=True)

    non_numeric = X_train.select_dtypes(exclude=["number", "bool"]).columns.tolist()
    if non_numeric:
        X_train.drop(columns=non_numeric, inplace=True)
        X_val.drop(columns=non_numeric, inplace=True)
        X_test.drop(columns=non_numeric, inplace=True)

    bool_cols = X_train.select_dtypes(include=["bool"]).columns.tolist()
    if bool_cols:
        X_train[bool_cols] = X_train[bool_cols].astype(int)
        X_val[bool_cols] = X_val[bool_cols].astype(int)
        X_test[bool_cols] = X_test[bool_cols].astype(int)

    feature_names = X_train.columns.tolist()
    print(f"  Final feature count: {len(feature_names)}", flush=True)

    # ── 4. Optuna hyperparameter searches (150 trials each) ────────────────
    print("\n[4/8] Running Optuna searches (150 trials per model)...", flush=True)
    N_TRIALS = 150
    # Downsample more aggressively to make 450 trials complete in reasonable time
    X_train_opt = X_train.sample(frac=0.01, random_state=42)
    y_train_opt = y_train.loc[X_train_opt.index]
    X_val_opt = X_val.sample(frac=0.02, random_state=42)
    y_val_opt = y_val.loc[X_val_opt.index]

    best_params = {}

    # XGBoost
    print("  -> XGBoost", flush=True)
    def obj_xgb(trial):
        params = {
            "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.1, log=True),
            "max_depth": trial.suggest_int("max_depth", 3, 8),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "min_child_weight": trial.suggest_int("min_child_weight", 5, 50),
            "objective": "binary:logistic", "eval_metric": "auc",
            "n_estimators": 100, "early_stopping_rounds": 15,
            "scale_pos_weight": scale_pos_weight, "tree_method": "hist", "verbosity": 0
        }
        model = xgb.XGBClassifier(**params)
        model.fit(X_train_opt, y_train_opt, eval_set=[(X_val_opt, y_val_opt)], verbose=False)
        auc = roc_auc_score(y_val_opt, model.predict_proba(X_val_opt)[:, 1])
        del model; gc.collect()
        return auc
    
    study_xgb = optuna.create_study(direction="maximize")
    study_xgb.optimize(obj_xgb, n_trials=N_TRIALS, show_progress_bar=True)
    best_params['xgb'] = study_xgb.best_params

    # LightGBM
    print("  -> LightGBM", flush=True)
    def obj_lgb(trial):
        params = {
            "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.1, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 15, 255),
            "max_depth": trial.suggest_int("max_depth", -1, 8),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "min_child_samples": trial.suggest_int("min_child_samples", 5, 50),
            "objective": "binary", "metric": "auc",
            "n_estimators": 100,
            "scale_pos_weight": scale_pos_weight, "verbose": -1
        }
        model = lgb.LGBMClassifier(**params)
        model.fit(X_train_opt, y_train_opt, eval_set=[(X_val_opt, y_val_opt)], callbacks=[lgb.early_stopping(15, verbose=False)])
        auc = roc_auc_score(y_val_opt, model.predict_proba(X_val_opt)[:, 1])
        del model; gc.collect()
        return auc

    study_lgb = optuna.create_study(direction="maximize")
    study_lgb.optimize(obj_lgb, n_trials=N_TRIALS, show_progress_bar=True)
    best_params['lgb'] = study_lgb.best_params

    # CatBoost
    print("  -> CatBoost", flush=True)
    def obj_cb(trial):
        params = {
            "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.1, log=True),
            "depth": trial.suggest_int("depth", 3, 8),
            "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 1, 10),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "iterations": 100, "eval_metric": "AUC",
            "scale_pos_weight": scale_pos_weight, "verbose": 0, "bootstrap_type": "Bernoulli"
        }
        model = CatBoostClassifier(**params)
        model.fit(X_train_opt, y_train_opt, eval_set=(X_val_opt, y_val_opt), early_stopping_rounds=15, verbose=False)
        auc = roc_auc_score(y_val_opt, model.predict_proba(X_val_opt)[:, 1])
        del model; gc.collect()
        return auc

    study_cb = optuna.create_study(direction="maximize")
    study_cb.optimize(obj_cb, n_trials=N_TRIALS, show_progress_bar=True)
    best_params['cb'] = study_cb.best_params

    print(f"\n  Best Val AUCs - XGB: {study_xgb.best_value:.4f}, LGB: {study_lgb.best_value:.4f}, CB: {study_cb.best_value:.4f}", flush=True)

    # ── 5. Train final models and generate OOF predictions ────────────────
    print("\n[5/8] Generating OOF predictions and training final models...", flush=True)
    
    final_xgb_params = {**best_params['xgb'], "objective": "binary:logistic", "eval_metric": "auc", "n_estimators": 1000, "early_stopping_rounds": 50, "scale_pos_weight": scale_pos_weight, "tree_method": "hist", "verbosity": 0}
    final_lgb_params = {**best_params['lgb'], "objective": "binary", "metric": "auc", "n_estimators": 1000, "scale_pos_weight": scale_pos_weight, "verbose": -1}
    final_cb_params = {**best_params['cb'], "iterations": 1000, "eval_metric": "AUC", "scale_pos_weight": scale_pos_weight, "verbose": 0, "bootstrap_type": "Bernoulli"}

    # Initialize OOF prediction arrays
    oof_xgb = np.zeros(len(X_train))
    oof_lgb = np.zeros(len(X_train))
    oof_cb = np.zeros(len(X_train))

    kf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    
    print("  Running 5-fold CV for OOF predictions...", flush=True)
    for fold, (train_idx, val_idx) in enumerate(kf.split(X_train, y_train)):
        print(f"    Fold {fold+1}/5", flush=True)
        X_tr, y_tr = X_train.iloc[train_idx], y_train.iloc[train_idx]
        X_v, y_v = X_train.iloc[val_idx], y_train.iloc[val_idx]

        # XGB
        m_xgb = xgb.XGBClassifier(**final_xgb_params)
        m_xgb.fit(X_tr, y_tr, eval_set=[(X_v, y_v)], verbose=False)
        oof_xgb[val_idx] = m_xgb.predict_proba(X_v)[:, 1]

        # LGB
        m_lgb = lgb.LGBMClassifier(**final_lgb_params)
        m_lgb.fit(X_tr, y_tr, eval_set=[(X_v, y_v)], callbacks=[lgb.early_stopping(50, verbose=False)])
        oof_lgb[val_idx] = m_lgb.predict_proba(X_v)[:, 1]

        # CB
        m_cb = CatBoostClassifier(**final_cb_params)
        m_cb.fit(X_tr, y_tr, eval_set=(X_v, y_v), early_stopping_rounds=50, verbose=False)
        oof_cb[val_idx] = m_cb.predict_proba(X_v)[:, 1]

    print("  Training final base models on full train set...", flush=True)
    model_xgb = xgb.XGBClassifier(**final_xgb_params)
    model_xgb.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
    
    model_lgb = lgb.LGBMClassifier(**final_lgb_params)
    model_lgb.fit(X_train, y_train, eval_set=[(X_val, y_val)], callbacks=[lgb.early_stopping(50, verbose=False)])

    model_cb = CatBoostClassifier(**final_cb_params)
    model_cb.fit(X_train, y_train, eval_set=(X_val, y_val), early_stopping_rounds=50, verbose=False)

    # ── 6. Train Meta-Learner ──────────────────────────────────────────────
    print("\n[6/8] Training LogisticRegression meta-learner...", flush=True)
    
    OOF_train = np.column_stack([oof_xgb, oof_lgb, oof_cb])
    meta_model = LogisticRegression(random_state=42)
    meta_model.fit(OOF_train, y_train)

    print(f"  Meta-learner coefficients: XGB={meta_model.coef_[0][0]:.4f}, LGB={meta_model.coef_[0][1]:.4f}, CB={meta_model.coef_[0][2]:.4f}", flush=True)

    # ── 7. Evaluate Ensemble on all splits ─────────────────────────────────
    print("\n[7/8] Evaluating Stacked Ensemble vs Static Average...", flush=True)

    splits = {
        "train": (X_train, y_train, group_train),
        "val": (X_val, y_val, group_val),
        "test": (X_test, y_test, group_test),
    }

    metrics = {}
    for split_name, (X, y, groups) in splits.items():
        pred_xgb = model_xgb.predict_proba(X)[:, 1]
        pred_lgb = model_lgb.predict_proba(X)[:, 1]
        pred_cb = model_cb.predict_proba(X)[:, 1]
        
        # Static average ensemble
        y_pred_static = (pred_xgb + pred_lgb + pred_cb) / 3.0
        
        # Stacked ensemble
        X_meta = np.column_stack([pred_xgb, pred_lgb, pred_cb])
        y_pred_stacked = meta_model.predict_proba(X_meta)[:, 1]

        auc_static = roc_auc_score(y, y_pred_static)
        auc_stacked = roc_auc_score(y, y_pred_stacked)
        ll = log_loss(y, y_pred_stacked)
        pak = precision_at_k(y, y_pred_stacked, groups, k=20) if groups is not None else None
        cal = calibration_summary(y, y_pred_stacked, n_bins=10)

        # Calculate per-source AUC
        try:
            with open(PROJECT_ROOT / "data" / "processed" / "encoders.pkl", "rb") as f:
                enc_data = pickle.load(f)
            encoder = enc_data["ordinal_encoder"]
            cat_cols = enc_data["cat_cols"]
            
            source_col = X["source"]
            sa_mask = pd.Series(False, index=source_col.index)
            gppd_mask = pd.Series(False, index=source_col.index)
            
            if "source" in cat_cols:
                idx = cat_cols.index("source")
                cats = list(encoder.categories_[idx])
                if "SA" in cats:
                    sa_val = cats.index("SA")
                    sa_mask = source_col == sa_val
                if "GPPD" in cats:
                    gppd_val = cats.index("GPPD")
                    gppd_mask = source_col == gppd_val
            
            auc_sa_stacked = roc_auc_score(y[sa_mask], y_pred_stacked[sa_mask]) if sum(sa_mask) > 0 and y[sa_mask].nunique() > 1 else None
            auc_gppd_stacked = roc_auc_score(y[gppd_mask], y_pred_stacked[gppd_mask]) if sum(gppd_mask) > 0 and y[gppd_mask].nunique() > 1 else None
            
            auc_sa_static = roc_auc_score(y[sa_mask], y_pred_static[sa_mask]) if sum(sa_mask) > 0 and y[sa_mask].nunique() > 1 else None
            auc_gppd_static = roc_auc_score(y[gppd_mask], y_pred_static[gppd_mask]) if sum(gppd_mask) > 0 and y[gppd_mask].nunique() > 1 else None
        except Exception as e:
            print(f"    Source AUC calc failed: {e}", flush=True)
            auc_sa_stacked, auc_gppd_stacked = None, None
            auc_sa_static, auc_gppd_static = None, None

        metrics[split_name] = {
            "roc_auc": round(auc_stacked, 6),
            "log_loss": round(ll, 6),
            "precision_at_20": round(pak, 6) if pak is not None else None,
            "n_samples": int(len(y)),
            "calibration": cal,
        }

        print(f"\n  {split_name.upper()}:", flush=True)
        print(f"    Static ROC-AUC:        {auc_static:.6f}", flush=True)
        print(f"    Stacked ROC-AUC:       {auc_stacked:.6f}", flush=True)
        print(f"    Diff (Stacked - Static): {auc_stacked - auc_static:+.6f}", flush=True)
        print(f"    Per-Source (Stacked):  SA: {auc_sa_stacked}, GPPD: {auc_gppd_stacked}", flush=True)
        print(f"    Per-Source (Static):   SA: {auc_sa_static}, GPPD: {auc_gppd_static}", flush=True)
        print(f"    Log Loss:              {ll:.6f}", flush=True)

    metrics["best_hyperparameters"] = best_params
    metrics["n_features"] = len(feature_names)
    metrics["feature_names"] = feature_names

    # ── 8. Save models and metrics ─────────────────────────────────────────
    print("\n[8/8] Saving models and metrics...", flush=True)

    model_xgb.save_model(str(MODEL_DIR / "model_xgb_conquest.json"))
    model_lgb.booster_.save_model(str(MODEL_DIR / "model_lgb_conquest.txt"))
    model_cb.save_model(str(MODEL_DIR / "model_cb_conquest.cbm"))
    
    with open(MODEL_DIR / "stacking_meta_conquest.pkl", "wb") as f:
        pickle.dump(meta_model, f)

    with open(METRICS_PATH.with_name("metrics_conquest.json"), "w") as f:
        json.dump(metrics, f, indent=2, default=str)

    # Save to SQLite
    metrics_flat = {
        "train_auc": metrics["train"]["roc_auc"],
        "val_auc": metrics["val"]["roc_auc"],
        "test_auc": metrics["test"]["roc_auc"],
        "model_version": "conquest",
    }
    save_to_sqlite(pd.DataFrame([metrics_flat]), "model_metrics_conquest", str(DB_PATH))

    print(f"\n{'=' * 70}", flush=True)
    print(f"  TRAINING COMPLETE", flush=True)
    print(f"{'=' * 70}", flush=True)

if __name__ == "__main__":
    main()
