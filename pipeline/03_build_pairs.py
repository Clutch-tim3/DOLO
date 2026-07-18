#!/usr/bin/env python3
"""
03_build_pairs.py — Build one row per (lot, supplier) pair with did_win label.

The GPPD data already has one row per bid with bid_iswinning as the label,
so the data is ALREADY in (tender/lot, bidder) pair format.

Main work:
  1. Load raw_contracts.parquet
  2. Create pair_id = lot_id + '_' + bidder_masterid
  3. Map bid_iswinning → did_win (True→1, False→0)
  4. Assign pair_type (winner / real_loser)
  5. Compute temporal features (publish_year, publish_month, etc.)
  6. Check class balance; add synthetic negatives if win rate > 30%
  7. Save labeled_pairs.parquet + SQLite table 'labeled_pairs'
"""

import sys
import time
import sqlite3
import hashlib
from pathlib import Path

import pandas as pd
import duckdb
import numpy as np

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_PATH = PROJECT_ROOT / "data" / "processed" / "raw_contracts.parquet"
OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "labeled_pairs.parquet"
SQLITE_DB = PROJECT_ROOT / "data" / "procurement.db"

# ── Helpers ────────────────────────────────────────────────────────────────

def save_to_sqlite(df, table_name, db_path):
    """Save DataFrame to SQLite table (replace if exists)."""
    conn = sqlite3.connect(str(db_path))
    # Cap at 1,000,000 rows to prevent disk exhaustion
    if len(df) > 1000000:
        print(f"  (Note: Capping SQLite table '{table_name}' at 1,000,000 rows to prevent disk space exhaustion)", flush=True)
        df_save = df.iloc[:1000000]
    else:
        df_save = df
    df_save.to_sql(table_name, conn, if_exists='replace', index=False)
    conn.close()
    print(f"  → SQLite: {table_name} table saved to {db_path} ({len(df_save):,} rows)", flush=True)


def log(msg=""):
    print(msg, flush=True)


# ══════════════════════════════════════════════════════════════════════════
def main():
    t0 = time.time()
    log("=" * 80)
    log("  03_build_pairs.py — Build labeled (lot, supplier) pairs")
    log("=" * 80)
    log()

    # ── 1. Validate input ──────────────────────────────────────────────────
    if not INPUT_PATH.exists():
        log(f"ERROR: Input file not found: {INPUT_PATH}")
        log("  Run 02_clean.py (or equivalent) first to produce raw_contracts.parquet")
        sys.exit(1)

    log(f"Input:  {INPUT_PATH}")
    log(f"Output: {OUTPUT_PATH}")
    log()

    # ── 2. Load and transform with DuckDB ──────────────────────────────────
    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit = '400MB';")
    con.execute("SET threads = 4;")

    input_sql = str(INPUT_PATH).replace("'", "''")

    # Quick stats on the input
    row_count = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{input_sql}')"
    ).fetchone()[0]
    log(f"Input rows: {row_count:,}")

    # Check that required columns exist
    schema = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{input_sql}')"
    ).fetchall()
    col_names = {row[0] for row in schema}
    required = {'lot_id', 'bidder_masterid', 'bid_iswinning', 'tender_year',
                'buyer_country', 'tender_id'}
    missing = required - col_names
    if missing:
        log(f"ERROR: Missing required columns: {missing}")
        sys.exit(1)
    log(f"Schema OK — {len(col_names)} columns, all required columns present")
    log()

    # ── 3. Build pairs ─────────────────────────────────────────────────────
    log("─── Building pair records ──────────────────────────────────────")

    # Determine which optional columns are available
    has_firstcall = 'tender_publications_firstcallfortenderdate' in col_names
    has_biddeadline = 'tender_biddeadline' in col_names
    has_duration = 'lot_updateddurationdays' in col_names
    has_awarddecision = 'tender_awarddecisiondate' in col_names

    log(f"  tender_publications_firstcallfortenderdate: {'YES' if has_firstcall else 'NO'}")
    log(f"  tender_biddeadline: {'YES' if has_biddeadline else 'NO'}")
    log(f"  lot_updateddurationdays: {'YES' if has_duration else 'NO'}")
    log(f"  tender_awarddecisiondate: {'YES' if has_awarddecision else 'NO'}")
    log()

    # Build the temporal feature expressions
    publish_month_expr = (
        "EXTRACT(MONTH FROM TRY_CAST(tender_publications_firstcallfortenderdate AS DATE))::INTEGER"
        if has_firstcall else "NULL::INTEGER"
    )
    publish_quarter_expr = (
        "EXTRACT(QUARTER FROM TRY_CAST(tender_publications_firstcallfortenderdate AS DATE))::INTEGER"
        if has_firstcall else "NULL::INTEGER"
    )

    # deadline_days: days between call-for-tender and bid deadline, abs, capped at 365
    if has_firstcall and has_biddeadline:
        deadline_days_expr = (
            "LEAST(ABS(DATE_DIFF('day', "
            "  TRY_CAST(tender_publications_firstcallfortenderdate AS DATE), "
            "  TRY_CAST(tender_biddeadline AS DATE)"
            ")), 365)::INTEGER"
        )
    else:
        deadline_days_expr = "NULL::INTEGER"

    contract_duration_expr = (
        "lot_updateddurationdays::INTEGER" if has_duration else "NULL::INTEGER"
    )

    # Select columns to carry forward — keep all useful columns from upstream
    # plus the new pair-level features
    pairs_sql = f"""
    SELECT
        -- Pair identifiers
        lot_id || '_' || bidder_masterid                        AS pair_id,
        tender_id,
        lot_id,
        bidder_masterid,
        buyer_masterid,

        -- Label
        CASE WHEN bid_iswinning = TRUE THEN 1 ELSE 0 END       AS did_win,

        -- Pair type
        CASE
            WHEN bid_iswinning = TRUE THEN 'winner'
            ELSE 'real_loser'
        END                                                     AS pair_type,

        -- Temporal features
        tender_year::INTEGER                                    AS publish_year,
        {publish_month_expr}                                    AS publish_month,
        {publish_quarter_expr}                                  AS publish_quarter,
        {deadline_days_expr}                                    AS deadline_days,
        {contract_duration_expr}                                AS contract_duration_days,

        -- Carry forward key columns for downstream feature engineering
        buyer_country,
        {"tender_proceduretype," if "tender_proceduretype" in col_names else ""}
        {"tender_supplytype," if "tender_supplytype" in col_names else ""}
        {"tender_cpvs," if "tender_cpvs" in col_names else ""}
        {"tender_selectionmethod," if "tender_selectionmethod" in col_names else ""}
        {"tender_isframeworkagreement," if "tender_isframeworkagreement" in col_names else ""}
        {"tender_isdps," if "tender_isdps" in col_names else ""}
        tender_lotscount,
        tender_recordedbidscount,
        {"lot_bidscount," if "lot_bidscount" in col_names else ""}
        tender_awardcriteria_count,
        tender_description_length,
        lot_description_length,
        tender_personalrequirements_length,
        tender_technicalrequirements_length,
        tender_economicrequirements_length,
        tender_corrections_count,
        {"bid_priceUsd," if "bid_priceUsd" in col_names else ""}
        {"tender_digiwhist_price," if "tender_digiwhist_price" in col_names else ""}
        {"bid_digiwhist_price," if "bid_digiwhist_price" in col_names else ""}
        {"tender_estimatedpriceUsd," if "tender_estimatedpriceUsd" in col_names else ""}
        {"tender_finalpriceUsd," if "tender_finalpriceUsd" in col_names else ""}
        {"submission_period," if "submission_period" in col_names else ""}
        {"corr_singleb," if "corr_singleb" in col_names else ""}
        {"corr_proc," if "corr_proc" in col_names else ""}
        {"corr_subm," if "corr_subm" in col_names else ""}
        {"cri," if "cri" in col_names else ""}
        {"corr_buyer_concentration," if "corr_buyer_concentration" in col_names else ""}
        {"currency," if "currency" in col_names else ""}
        {"buyer_name," if "buyer_name" in col_names else ""}
        {"bidder_name," if "bidder_name" in col_names else ""}
        {"bid_issubcontracted," if "bid_issubcontracted" in col_names else ""}
        {"bid_isconsortium," if "bid_isconsortium" in col_names else ""}
        {"lot_status," if "lot_status" in col_names else ""}
        {"buyer_buyertype," if "buyer_buyertype" in col_names else ""}
        {"buyer_mainactivities," if "buyer_mainactivities" in col_names else ""}
        {"tender_awarddecisiondate," if has_awarddecision else ""}
        {"tender_publications_firstcallfortenderdate," if has_firstcall else ""}
        {"tender_biddeadline," if has_biddeadline else ""}
        {"filter_ok," if "filter_ok" in col_names else ""}
        {"filter_losingbids," if "filter_losingbids" in col_names else ""}
        persistent_id

    FROM read_parquet('{input_sql}')
    WHERE bid_iswinning IS NOT NULL
      AND lot_id IS NOT NULL
      AND bidder_masterid IS NOT NULL
      AND RANDOM() <= 0.02
    """

    # Create the pairs view
    con.execute(f"CREATE TABLE pairs AS {pairs_sql}")

    pairs_count = con.execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
    log(f"  Pairs created (2% sample): {pairs_count:,}")

    # Rows dropped due to nulls in required fields
    dropped = row_count - pairs_count
    log(f"  Rows filtered/dropped: {dropped:,}")
    log()

    # ── 4. Check class balance ─────────────────────────────────────────────
    log("─── Class Balance Check ────────────────────────────────────────")

    balance = con.execute("""
        SELECT
            did_win,
            COUNT(*)     AS cnt,
            pair_type
        FROM pairs
        GROUP BY did_win, pair_type
        ORDER BY did_win
    """).fetchdf()

    for _, row in balance.iterrows():
        log(f"  did_win={row['did_win']}: {row['cnt']:>12,}  ({row['pair_type']})")

    total = balance['cnt'].sum()
    wins = balance.loc[balance['did_win'] == 1, 'cnt'].sum()
    losses = balance.loc[balance['did_win'] == 0, 'cnt'].sum()
    win_rate = wins / total if total > 0 else 0

    log(f"\n  Total pairs:    {total:>12,}")
    log(f"  Winners (1):    {wins:>12,}  ({100*win_rate:.2f}%)")
    log(f"  Losers  (0):    {losses:>12,}  ({100*(1-win_rate):.2f}%)")
    log()

    # Load all pairs into pandas for memory-efficient shuffling
    df = con.execute("SELECT * FROM pairs").fetchdf()
    con.close() # Close duckdb memory database to free RAM

    # ── 5. Synthetic negatives if win rate > 30% ───────────────────────────
    if win_rate > 0.30:
        log("─── Win rate > 30% — Adding Synthetic Negatives ───────────────")
        log(f"  Current win rate: {100*win_rate:.2f}%")

        # Target: bring win rate down to ~15%
        # Formula: wins / (wins + losses + synth) = 0.15 => synth = wins/0.15 - wins - losses
        target_rate = 0.15
        synth_needed = int(wins / target_rate - wins - losses)
        log(f"  Synthetic negatives needed for ~{100*target_rate:.0f}% win rate: {synth_needed:,}")

        # Number of shuffles to perform
        synth_per_lot = max(1, synth_needed // len(df))
        synth_per_lot = min(synth_per_lot, 10)
        log(f"  Synthetic iterations (shuffles) per lot: {synth_per_lot}")

        synth_dfs = []
        # Group by country and year to shuffle within the same market
        group_cols = ['buyer_country', 'publish_year']
        
        for i in range(synth_per_lot):
            log(f"  Shuffling bidder pool (iteration {i+1}/{synth_per_lot})...")
            shuffled = df.copy()
            # Perform grouped permutations on bidder fields to keep the context realistic
            shuffled['bidder_masterid'] = df.groupby(group_cols)['bidder_masterid'].transform(np.random.permutation)
            if 'bidder_name' in df.columns:
                shuffled['bidder_name'] = df.groupby(group_cols)['bidder_name'].transform(np.random.permutation)
            
            # Reset labels
            shuffled['did_win'] = 0
            shuffled['pair_type'] = 'synthetic_negative'
            shuffled['pair_id'] = shuffled['lot_id'] + '_' + shuffled['bidder_masterid']
            
            # Filter out cases where the shuffled bidder is the same as the original
            shuffled = shuffled[shuffled['bidder_masterid'] != df['bidder_masterid']]
            synth_dfs.append(shuffled)

        # Concatenate synthetic negatives
        df_synth = pd.concat(synth_dfs, ignore_index=True)
        log(f"  Generated {len(df_synth):,} synthetic negatives.")

        # Combine real pairs and synthetic negatives
        df = pd.concat([df, df_synth], ignore_index=True)
        total = len(df)
        wins = (df['did_win'] == 1).sum()
        losses = (df['did_win'] == 0).sum()
        win_rate = wins / total

        log(f"\n  After synthetic negatives:")
        log(f"    Total pairs:  {total:>12,}")
        log(f"    Winners (1):  {wins:>12,}")
        log(f"    Win rate:     {100*win_rate:>11.2f}%")
        log()
    else:
        log(f"  Win rate ({100*win_rate:.2f}%) is ≤ 30% — no synthetic negatives needed ✓")
        log()

    # ── 6. Final class balance report ──────────────────────────────────────
    log("─── Final Class Balance Report ─────────────────────────────────")
    final_balance = df.groupby(['pair_type', 'did_win']).size().reset_index(name='cnt')
    for _, row in final_balance.iterrows():
        pct = 100 * row['cnt'] / total
        log(f"  {row['pair_type']:<22} did_win={row['did_win']}  "
            f"{row['cnt']:>12,}  ({pct:.2f}%)")

    scale_pos_weight = losses / wins if wins > 0 else 1.0
    log(f"\n  scale_pos_weight = {losses:,} / {wins:,} = {scale_pos_weight:.4f}")
    log(f"  (Use this in XGBoost/LightGBM for class imbalance)")
    log()

    # ── 7. Year distribution ───────────────────────────────────────────────
    log("─── Year Distribution ──────────────────────────────────────────")
    year_dist = df.groupby('publish_year').agg(
        n_pairs=('did_win', 'count'),
        n_wins=('did_win', 'sum')
    ).reset_index()
    year_dist['win_pct'] = 100.0 * year_dist['n_wins'] / year_dist['n_pairs']
    
    log(f"  {'Year':<8} {'Pairs':>12} {'Wins':>10} {'Win%':>8}")
    log(f"  {'─'*8} {'─'*12} {'─'*10} {'─'*8}")
    for _, row in year_dist.iterrows():
        log(f"  {int(row['publish_year']):<8} {int(row['n_pairs']):>12,} "
            f"{int(row['n_wins']):>10,} {row['win_pct']:>7.2f}%")
    log()

    # ── 8. Save to Parquet ─────────────────────────────────────────────────
    log("─── Saving Output ──────────────────────────────────────────────")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT_PATH, compression='zstd')
    
    file_size_mb = OUTPUT_PATH.stat().st_size / (1024 ** 2)
    log(f"  Parquet: {OUTPUT_PATH}")
    log(f"  Rows:    {len(df):,}")
    log(f"  Size:    {file_size_mb:.1f} MB")
    log()

    # ── 9. Save to SQLite ──────────────────────────────────────────────────
    log("─── Saving to SQLite ───────────────────────────────────────────")
    save_to_sqlite(df, "labeled_pairs", SQLITE_DB)
    log()

    # ── 10. Summary statistics ─────────────────────────────────────────────
    log("─── Column Summary ─────────────────────────────────────────────")
    log(f"  {'#':<4} {'Column':<50} {'Type':<18}")
    log(f"  {'─'*4} {'─'*50} {'─'*18}")
    for i, col in enumerate(df.columns):
        log(f"  {i+1:<4} {col:<50} {str(df[col].dtype):<18}")
    log(f"\n  Total columns: {len(df.columns)}")
    log()

    # Null rates for key new columns
    log("  Key column null rates:")
    for col in ['pair_id', 'did_win', 'pair_type', 'publish_year',
                'publish_month', 'publish_quarter', 'deadline_days',
                'contract_duration_days']:
        if col in df.columns:
            null_pct = 100.0 * df[col].isnull().sum() / len(df)
            log(f"    {col:<35} {null_pct:>6.2f}% null")
    log()

    # ── Done ───────────────────────────────────────────────────────────────
    elapsed = time.time() - t0
    log("=" * 80)
    log("  03_build_pairs.py — COMPLETE")
    log(f"  Total pairs:          {len(df):,}")
    log(f"  Winners (did_win=1):  {wins:,} ({100*win_rate:.2f}%)")
    log(f"  Losers  (did_win=0):  {losses:,} ({100*(1-win_rate):.2f}%)")
    log(f"  scale_pos_weight:     {scale_pos_weight:.4f}")
    log(f"  Output:               {OUTPUT_PATH}")
    log(f"  SQLite:               {SQLITE_DB} → labeled_pairs")
    log(f"  Elapsed:              {elapsed:.1f}s")
    log("=" * 80)


if __name__ == "__main__":
    main()
