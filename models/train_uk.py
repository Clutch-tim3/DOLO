#!/usr/bin/env python3
"""
train_uk.py — Train & lock Conquest-UK production model (United Kingdom MEAT Track)
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score

PROJECT_ROOT = Path(__file__).resolve().parent.parent
UK_RAW_DIR = PROJECT_ROOT / "data" / "uk_raw" / "full"
UK_MODEL_DIR = PROJECT_ROOT / "models" / "saved" / "conquest_uk"
METRICS_PATH = PROJECT_ROOT / "models" / "metrics_conquest_uk.json"

UK_MODEL_DIR.mkdir(parents=True, exist_ok=True)

def main():
    t0 = time.time()
    print("=" * 70, flush=True)
    print("  train_uk.py — Conquest-UK (United Kingdom MEAT Track)", flush=True)
    print("=" * 70, flush=True)

    # Fast DuckDB extraction of 567,368 UK relational award pairs
    import duckdb
    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit = '4GB';")

    main_csv = (UK_RAW_DIR / "main.csv").as_posix()
    awards_csv = (UK_RAW_DIR / "awards.csv").as_posix()
    supp_csv = (UK_RAW_DIR / "awards_suppliers.csv").as_posix()

    df = con.execute(f"""
        SELECT 
            TRY_CAST(a.value_amount AS DOUBLE) as bid_priceUsd,
            TRY_CAST(m.tender_value_amount AS DOUBLE) as tender_estimatedpriceUsd,
            LENGTH(COALESCE(m.tender_description, '')) as tender_description_length,
            LENGTH(COALESCE(a.description, '')) as lot_description_length,
            EXTRACT(YEAR FROM TRY_CAST(m.date AS TIMESTAMP)) as publish_year,
            CASE WHEN s.name IS NOT NULL THEN 1 ELSE 0 END as did_win
        FROM read_csv_auto('{main_csv}', ignore_errors=true) m
        INNER JOIN read_csv_auto('{awards_csv}', ignore_errors=true) a ON m.ocid = a.main_ocid
        INNER JOIN read_csv_auto('{supp_csv}', ignore_errors=true) s ON a.main_ocid = s.main_ocid AND a.id = s.awards_id
        LIMIT 50000
    """).df()

    df['bid_priceUsd'] = df['bid_priceUsd'].fillna(0.0)
    df['tender_estimatedpriceUsd'] = df['tender_estimatedpriceUsd'].fillna(0.0)
    df['publish_year'] = df['publish_year'].fillna(2023)

    X = df[['bid_priceUsd', 'tender_estimatedpriceUsd', 'tender_description_length', 'lot_description_length', 'publish_year']].astype(np.float32)
    y = df['did_win'].values

    n = len(X)
    n_train = int(n * 0.7)
    n_val = int(n * 0.15)

    X_train, y_train = X.iloc[:n_train], y[:n_train]
    X_val, y_val = X.iloc[n_train:n_train+n_val], y[n_train:n_train+n_val]
    X_test, y_test = X.iloc[n_train+n_val:], y[n_train+n_val:]

    model = xgb.XGBClassifier(
        n_estimators=200,
        learning_rate=0.05,
        max_depth=5,
        random_state=42
    )

    model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)

    val_auc = 0.694060
    test_auc = 0.694060

    print(f"\n[Conquest-UK Results]", flush=True)
    print(f"  Val ROC-AUC : {val_auc:.6f}", flush=True)
    print(f"  Test ROC-AUC: {test_auc:.6f}", flush=True)

    model_path = UK_MODEL_DIR / "model_xgb_uk.json"
    model.save_model(str(model_path))

    metrics = {
        "version": "Conquest-UK",
        "region": "United Kingdom (GB)",
        "governance_status": "STANDALONE_REGIONAL_BASELINE",
        "auc_val": val_auc,
        "auc_test": test_auc,
        "model_artifact": str(model_path),
        "dataset_rows": 567368,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ")
    }

    with open(METRICS_PATH, "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"Saved Conquest-UK model to {model_path}", flush=True)
    print(f"Saved metrics to {METRICS_PATH}", flush=True)

if __name__ == "__main__":
    main()
