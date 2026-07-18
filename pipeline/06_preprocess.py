#!/usr/bin/env python3
"""
06_preprocess.py — Encode categoricals, impute nulls, save final train/val/test splits.

Steps:
  1. Load master_training_dataset.parquet
  2. Separate features from excluded / ID / target columns
  3. OrdinalEncode categoricals (fit on train split only)
  4. Median-impute numerics (medians from train split only)
  5. Save X_train / X_val / X_test and y_train / y_val / y_test
     → data/splits/*.parquet  AND  SQLite tables
  6. Persist fitted encoder + medians as pickle files
"""

import sys
import time
import pickle
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import OrdinalEncoder

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
SPLITS_DIR = DATA_DIR / "splits"
SQLITE_DB = DATA_DIR / "procurement.db"

MASTER_PATH = PROCESSED_DIR / "master_training_dataset.parquet"

SPLITS_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# ── Columns to EXCLUDE from features ──────────────────────────────────────
EXCLUDE_COLS = [
    "pair_id",
    "tender_id",
    "lot_id",
    "bid_id",
    "persistent_id",
    "bidder_masterid",
    "bidder_name",
    "buyer_masterid",
    "buyer_name",
    "split",
    "did_win",
    "pair_type",
    "tender_publications_firstcallfortenderdate",
    "tender_awarddecisiondate",
    "tender_biddeadline",
    "source_country",
    "tender_cpvs",
    "currency",
]

TARGET_COL = "did_win"
SPLIT_COL = "split"


# ── SQLite helper ─────────────────────────────────────────────────────────
def save_to_sqlite(df, table_name, db_path):
    """Save DataFrame to SQLite table (replace if exists)."""
    conn = sqlite3.connect(str(db_path))
    df.to_sql(table_name, conn, if_exists="replace", index=False)
    conn.close()
    print(f"  → SQLite: {table_name} table saved to {db_path}", flush=True)


# ══════════════════════════════════════════════════════════════════════════
def main():
    t0 = time.time()
    print("=" * 80, flush=True)
    print("  06_preprocess — Encode, Impute, Split", flush=True)
    print("=" * 80, flush=True)

    # ── 1. Load master dataset ─────────────────────────────────────────────
    print(f"\n[1/8] Loading {MASTER_PATH.name} …", flush=True)
    if not MASTER_PATH.exists():
        print(f"ERROR: {MASTER_PATH} does not exist. Run previous pipeline steps first.", flush=True)
        sys.exit(1)

    df = pd.read_parquet(MASTER_PATH)
    print(f"  Loaded {len(df):,} rows × {len(df.columns)} columns", flush=True)

    # Validate required columns
    for req in [TARGET_COL, SPLIT_COL]:
        if req not in df.columns:
            print(f"ERROR: Required column '{req}' not in dataset.", flush=True)
            sys.exit(1)

    # ── 2. Identify feature columns ───────────────────────────────────────
    print("\n[2/8] Identifying feature columns …", flush=True)
    exclude_present = [c for c in EXCLUDE_COLS if c in df.columns]
    exclude_missing = [c for c in EXCLUDE_COLS if c not in df.columns]
    if exclude_missing:
        print(f"  Note: {len(exclude_missing)} exclude columns not in data: {exclude_missing}", flush=True)

    feature_cols = [c for c in df.columns if c not in EXCLUDE_COLS]
    print(f"  Excluded columns: {len(exclude_present)}", flush=True)
    print(f"  Feature columns:  {len(feature_cols)}", flush=True)

    # To save memory, drop all excluded columns from df except did_win and split
    cols_to_drop = [c for c in df.columns if c not in feature_cols and c not in [TARGET_COL, SPLIT_COL]]
    if cols_to_drop:
        print(f"  Dropping {len(cols_to_drop)} unused columns from memory...", flush=True)
        df.drop(columns=cols_to_drop, inplace=True)
    import gc
    gc.collect()

    # ── 2b. Coerce string columns that are actually numeric ───────────────
    print("\n[2b/8] Coercing numeric-looking string columns …", flush=True)
    coerced = 0
    for col in feature_cols:
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col].dtype):
            converted = pd.to_numeric(df[col], errors="coerce")
            # If >50% of non-null values converted successfully, treat as numeric
            non_null_orig = df[col].notna().sum()
            non_null_conv = converted.notna().sum()
            if non_null_orig > 0 and non_null_conv / non_null_orig > 0.5:
                df[col] = converted.astype("float32")
                coerced += 1
                print(f"  {col} → float32 ({non_null_conv:,}/{non_null_orig:,} converted)", flush=True)
    print(f"  Coerced {coerced} columns from string to numeric", flush=True)
    gc.collect()

    # ── 3. Split by train / val / test ────────────────────────────────────
    print("\n[3/8] Splitting by 'split' column …", flush=True)
    split_values = df[SPLIT_COL].unique()
    print(f"  Split values found: {sorted(split_values)}", flush=True)

    mask_train = df[SPLIT_COL] == "train"
    mask_val = df[SPLIT_COL] == "val"
    mask_test = df[SPLIT_COL] == "test"

    print(f"  Train: {mask_train.sum():,} rows", flush=True)
    print(f"  Val:   {mask_val.sum():,} rows", flush=True)
    print(f"  Test:  {mask_test.sum():,} rows", flush=True)

    if mask_train.sum() == 0:
        print("ERROR: No training rows found.", flush=True)
        sys.exit(1)

    # ── 4. Identify categorical vs numeric columns ────────────────────────
    print("\n[4/8] Identifying categorical columns …", flush=True)
    cat_cols = []
    num_cols = []
    bool_cols = []
    for col in feature_cols:
        dtype = df[col].dtype
        if dtype == object or pd.api.types.is_string_dtype(dtype):
            cat_cols.append(col)
        elif pd.api.types.is_bool_dtype(dtype):
            bool_cols.append(col)
        else:
            num_cols.append(col)

    print(f"  Categorical columns ({len(cat_cols)}):", flush=True)
    for c in cat_cols:
        n_unique = df[c].nunique(dropna=True)
        null_pct = 100.0 * df[c].isna().sum() / len(df)
        print(f"    {c:<50s} {n_unique:>6,} unique  {null_pct:>6.1f}% null", flush=True)

    print(f"  Boolean columns ({len(bool_cols)}): {bool_cols}", flush=True)
    print(f"  Numeric columns ({len(num_cols)}): {len(num_cols)} total", flush=True)

    # ── 5. Convert booleans to int ────────────────────────────────────────
    print("\n[5/8] Converting booleans to int …", flush=True)
    for col in bool_cols:
        df[col] = df[col].astype("Int8")  # nullable integer
        print(f"  {col} → Int8", flush=True)
    # Move bool cols into numeric list for imputation
    num_cols.extend(bool_cols)

    # ── 6. Categorical encoding (OrdinalEncoder, train-fit only) ──────────
    print("\n[6/8] Encoding categoricals with OrdinalEncoder …", flush=True)
    if cat_cols:
        encoder = OrdinalEncoder(
            handle_unknown="use_encoded_value",
            unknown_value=-1,
            encoded_missing_value=-2,   # explicit code for NaN
        )

        # Fit on training data only (fit on copy to prevent side effects)
        encoder.fit(df.loc[mask_train, cat_cols])

        print(f"  Fitted OrdinalEncoder on training rows", flush=True)
        for i, col in enumerate(cat_cols):
            n_cats = len(encoder.categories_[i])
            print(f"    {col:<50s} {n_cats:>5} categories learned", flush=True)

        # Transform entire column in-place
        df[cat_cols] = encoder.transform(df[cat_cols])
        print(f"  Transformed all rows in-place", flush=True)

        # Cast categorical columns to float32 (they are ordinal-encoded numbers now)
        for col in cat_cols:
            df[col] = df[col].astype("float32")

        # Save encoder
        enc_path = PROCESSED_DIR / "encoders.pkl"
        with open(enc_path, "wb") as f:
            pickle.dump({"ordinal_encoder": encoder, "cat_cols": cat_cols}, f)
        print(f"  Encoder saved → {enc_path}", flush=True)
        gc.collect()
    else:
        encoder = None
        print("  No categorical columns to encode.", flush=True)

    # ── 7. Numeric imputation (train medians) ─────────────────────────────
    print("\n[7/8] Imputing numeric nulls with training medians …", flush=True)

    medians = {}
    null_before = {}
    # Impute numeric columns column-by-column to avoid creating large memory slices
    for col in num_cols:
        n_null = df[col].isna().sum()
        null_before[col] = n_null
        if n_null > 0:
            med_val = df.loc[mask_train, col].median()
            # If median is NaN (all training values null), use 0
            if pd.isna(med_val):
                med_val = 0.0
            medians[col] = float(med_val)
            df[col] = df[col].fillna(med_val)

    # Also impute any remaining nulls in cat_cols (encoded_missing_value = -2)
    for col in cat_cols:
        n_null = df[col].isna().sum()
        null_before[col] = n_null
        if n_null > 0:
            medians[col] = -2.0  # consistent with encoded_missing_value
            df[col] = df[col].fillna(-2.0)

    cols_with_nulls = {c: n for c, n in null_before.items() if n > 0}
    print(f"  Columns with nulls: {len(cols_with_nulls)}", flush=True)
    for col, n in sorted(cols_with_nulls.items(), key=lambda x: -x[1])[:20]:
        pct = 100.0 * n / len(df)
        fill = medians.get(col, "N/A")
        print(f"    {col:<50s} {n:>10,} nulls ({pct:>5.1f}%)  fill={fill}", flush=True)
    if len(cols_with_nulls) > 20:
        print(f"    … and {len(cols_with_nulls) - 20} more columns", flush=True)

    # Save medians
    med_path = PROCESSED_DIR / "medians.pkl"
    with open(med_path, "wb") as f:
        pickle.dump(medians, f)
    print(f"  Medians dict saved → {med_path}  ({len(medians)} entries)", flush=True)
    gc.collect()

    # Cast all remaining numerics to float32 to save memory
    for col in num_cols:
        df[col] = df[col].astype("float32")
    gc.collect()

    # ── 8. Save splits ───────────────────────────────────────────────────
    print("\n[8/8] Saving train / val / test splits …", flush=True)

    y = df[TARGET_COL].astype(int)

    splits = {
        "train": mask_train,
        "val": mask_val,
        "test": mask_test,
    }

    for split_name, mask in splits.items():
        X_split = df.loc[mask, feature_cols].reset_index(drop=True)
        y_split = y.loc[mask].reset_index(drop=True).to_frame(name="did_win")

        # Parquet
        x_path = SPLITS_DIR / f"X_{split_name}.parquet"
        y_path = SPLITS_DIR / f"y_{split_name}.parquet"
        X_split.to_parquet(x_path, index=False)
        y_split.to_parquet(y_path, index=False)
        print(f"  {split_name}: X={X_split.shape}  y={y_split.shape}", flush=True)
        print(f"    → {x_path.name}, {y_path.name}", flush=True)

        # SQLite skipped for splits — Parquet files are sufficient for training
        # and the DB is near disk capacity limits
        print(f"    (SQLite skipped to conserve disk space)", flush=True)

    # ── Verification: null check ──────────────────────────────────────────
    print("\n" + "─" * 80, flush=True)
    print("  VERIFICATION — Null rates in final features (should be zero)", flush=True)
    print("─" * 80, flush=True)

    # Check on training split as representative
    X_check = pd.read_parquet(SPLITS_DIR / "X_train.parquet")
    remaining_nulls = X_check.isnull().sum()
    cols_still_null = remaining_nulls[remaining_nulls > 0]
    if len(cols_still_null) == 0:
        print("  ✓ All null rates are ZERO across all feature columns.", flush=True)
    else:
        print(f"  ⚠ {len(cols_still_null)} columns still have nulls:", flush=True)
        for col, n in cols_still_null.items():
            print(f"    {col}: {n:,} nulls", flush=True)

    # ── Feature list and dtypes ───────────────────────────────────────────
    print("\n" + "─" * 80, flush=True)
    print("  FINAL FEATURE LIST", flush=True)
    print("─" * 80, flush=True)
    print(f"  {'#':<4} {'Feature':<50} {'Dtype':<12} {'Null%':>6}", flush=True)
    print(f"  {'─'*4} {'─'*50} {'─'*12} {'─'*6}", flush=True)
    for i, col in enumerate(X_check.columns):
        dtype_str = str(X_check[col].dtype)
        null_pct = 100.0 * X_check[col].isna().sum() / len(X_check)
        print(f"  {i+1:<4} {col:<50} {dtype_str:<12} {null_pct:>5.1f}%", flush=True)

    # ── Summary ───────────────────────────────────────────────────────────
    elapsed = time.time() - t0
    print("\n" + "=" * 80, flush=True)
    print("  06_preprocess COMPLETE", flush=True)
    print(f"  Total features: {len(feature_cols)}", flush=True)
    print(f"    Categorical (ordinal-encoded): {len(cat_cols)}", flush=True)
    print(f"    Boolean (→ int):               {len(bool_cols)}", flush=True)
    print(f"    Numeric:                       {len(num_cols) - len(bool_cols)}", flush=True)
    print(f"  Imputed columns:  {len(medians)}", flush=True)
    print(f"  Splits saved to:  {SPLITS_DIR}", flush=True)
    print(f"  Encoder saved to: {PROCESSED_DIR / 'encoders.pkl'}", flush=True)
    print(f"  Medians saved to: {PROCESSED_DIR / 'medians.pkl'}", flush=True)
    print(f"  SQLite DB:        {SQLITE_DB}", flush=True)
    print(f"  Elapsed: {elapsed:.1f}s", flush=True)
    print("=" * 80, flush=True)


if __name__ == "__main__":
    main()
