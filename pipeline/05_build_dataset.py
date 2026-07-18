#!/usr/bin/env python3
"""
05_build_dataset.py — Build master training dataset with time-based split.

Joins labeled_pairs with supplier_features, engineers competition and
point-in-time buyer-history features, then splits by tender_year into
train / val / test sets (~70/15/15).

Outputs:
  - data/processed/master_training_dataset.parquet
  - data/procurement.db  → table 'master_training_dataset'
  - data/processed/dataset_metadata.json
"""

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DB_PATH = PROJECT_ROOT / "data" / "procurement.db"

LABELED_PATH = PROCESSED_DIR / "labeled_pairs.parquet"
SUPPLIER_PATH = PROCESSED_DIR / "supplier_features.parquet"
OUTPUT_PATH = PROCESSED_DIR / "master_training_dataset.parquet"
METADATA_PATH = PROCESSED_DIR / "dataset_metadata.json"


# ── Helpers ────────────────────────────────────────────────────────────────
def save_to_sqlite(df, table_name, db_path):
    """Save DataFrame to SQLite table (replace if exists)."""
    conn = sqlite3.connect(str(db_path))
    df.to_sql(table_name, conn, if_exists='replace', index=False)
    conn.close()
    print(f"  → SQLite: {table_name} table saved to {db_path}", flush=True)


def check_inputs():
    """Verify upstream parquet files exist."""
    missing = []
    for p in [LABELED_PATH, SUPPLIER_PATH]:
        if not p.exists():
            missing.append(str(p))
    if missing:
        print("ERROR: Missing upstream files:", flush=True)
        for m in missing:
            print(f"  • {m}", flush=True)
        print("\nRun pipeline steps 02-04 first.", flush=True)
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════
def build_dataset():
    t_start = time.time()
    check_inputs()

    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit = '8GB';")
    con.execute("PRAGMA max_temp_directory_size='20GiB';")
    con.execute("SET threads = 4;")

    # ── 1. Load upstream parquets ──────────────────────────────────────────
    print("=" * 72, flush=True)
    print("  05_build_dataset — Master Training Dataset Builder", flush=True)
    print("=" * 72, flush=True)

    lp_path = str(LABELED_PATH).replace("'", "''")
    sf_path = str(SUPPLIER_PATH).replace("'", "''")

    lp_count = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{lp_path}')"
    ).fetchone()[0]
    print(f"\n[1/7] Loaded labeled_pairs: {lp_count:,} rows", flush=True)

    sf_count = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{sf_path}')"
    ).fetchone()[0]
    print(f"       Loaded supplier_features: {sf_count:,} rows", flush=True)

    # Show columns for debugging
    lp_cols = [r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{lp_path}')"
    ).fetchall()]
    sf_cols = [r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{sf_path}')"
    ).fetchall()]
    print(f"       labeled_pairs columns ({len(lp_cols)}): {lp_cols[:10]}...", flush=True)
    print(f"       supplier_features columns ({len(sf_cols)}): {sf_cols[:10]}...", flush=True)

    # ── 2. Register as views ───────────────────────────────────────────────
    con.execute(f"CREATE VIEW labeled_pairs AS SELECT * FROM read_parquet('{lp_path}')")
    con.execute(f"CREATE VIEW supplier_features AS SELECT * FROM read_parquet('{sf_path}')")

    # ── 3. Join labeled_pairs ← supplier_features on pair_id ───────────────
    print("\n[2/7] Joining labeled_pairs with supplier_features on pair_id...", flush=True)

    # Identify columns to bring from supplier_features (exclude pair_id to avoid dup)
    sf_join_cols = [c for c in sf_cols if c != "pair_id"]
    sf_select = ", ".join([f's."{c}"' for c in sf_join_cols])

    con.execute(f"""
        CREATE TABLE joined AS
        SELECT lp.*, {sf_select}
        FROM labeled_pairs lp
        LEFT JOIN supplier_features s
          ON lp.pair_id = s.pair_id
    """)

    joined_count = con.execute("SELECT COUNT(*) FROM joined").fetchone()[0]
    matched = con.execute("""
        SELECT COUNT(*) FROM joined
        WHERE pair_id IN (SELECT pair_id FROM supplier_features)
    """).fetchone()[0]
    print(f"       Joined rows: {joined_count:,}  (matched supplier features: {matched:,})", flush=True)

    # Add tender_size_ratio
    print("       Computing tender_size_ratio...", flush=True)
    con.execute("""
        ALTER TABLE joined ADD COLUMN tender_size_ratio DOUBLE;
    """)
    con.execute("""
        UPDATE joined SET tender_size_ratio =
            CASE WHEN pit_avg_contract_value > 0 AND COALESCE(TRY_CAST(tender_estimatedpriceUsd AS DOUBLE), TRY_CAST(bid_priceUsd AS DOUBLE)) IS NOT NULL
                 THEN COALESCE(TRY_CAST(tender_estimatedpriceUsd AS DOUBLE), TRY_CAST(bid_priceUsd AS DOUBLE)) / pit_avg_contract_value
                 ELSE NULL END
    """)

    # ── 4. Competition features ────────────────────────────────────────────
    print("\n[3/7] Adding competition features (per lot_id)...", flush=True)

    # Check if lot_id column exists; fall back to tender_id if needed
    joined_cols = [r[0] for r in con.execute("DESCRIBE joined").fetchall()]
    if "lot_id" in joined_cols:
        comp_col = "lot_id"
    elif "tender_id" in joined_cols:
        comp_col = "tender_id"
        print("       ⚠ lot_id not found, using tender_id as competition unit", flush=True)
    else:
        print("       ERROR: Neither lot_id nor tender_id found in joined data", flush=True)
        sys.exit(1)

    con.execute(f"""
        ALTER TABLE joined ADD COLUMN actual_num_competitors INTEGER;
    """)
    con.execute(f"""
        UPDATE joined SET actual_num_competitors = sub.cnt
        FROM (
            SELECT "{comp_col}", COUNT(*) AS cnt
            FROM joined
            GROUP BY "{comp_col}"
        ) sub
        WHERE joined."{comp_col}" = sub."{comp_col}"
    """)

    con.execute("""
        ALTER TABLE joined ADD COLUMN competition_baseline DOUBLE;
    """)
    con.execute("""
        UPDATE joined SET competition_baseline =
            CASE WHEN actual_num_competitors > 0
                 THEN 1.0 / actual_num_competitors
                 ELSE NULL END
    """)

    comp_stats = con.execute("""
        SELECT
            MIN(actual_num_competitors) AS min_comp,
            AVG(actual_num_competitors) AS avg_comp,
            MAX(actual_num_competitors) AS max_comp,
            AVG(competition_baseline) AS avg_baseline
        FROM joined
    """).fetchone()
    print(f"       Competitors — min: {comp_stats[0]}, avg: {comp_stats[1]:.2f}, max: {comp_stats[2]}", flush=True)
    print(f"       Baseline win prob — avg: {comp_stats[3]:.4f}", flush=True)

    # ── 5. Buyer history features (point-in-time) ──────────────────────────
    print("\n[4/7] Computing point-in-time buyer history features...", flush=True)

    # Check required columns exist
    has_buyer_masterid = "buyer_masterid" in joined_cols
    has_publish_year = "publish_year" in joined_cols
    has_did_win = "did_win" in joined_cols
    has_bid_priceUsd = "bid_priceUsd" in joined_cols
    has_recorded_bids = "tender_recordedbidscount" in joined_cols

    if not has_buyer_masterid:
        print("       ⚠ buyer_masterid not found — skipping buyer history features", flush=True)
    elif not has_publish_year:
        print("       ⚠ publish_year not found — skipping buyer history features", flush=True)
    else:
        # Compute buyer history from winning bids in prior years (point-in-time)
        # buyer_total_past_awards: count of winning bids this buyer had in prior years
        # buyer_avg_contract_value: mean bid_priceUsd for winning bids by this buyer in prior years
        # buyer_avg_competitors: mean tender_recordedbidscount for this buyer in prior years

        price_col = "TRY_CAST(bid_priceUsd AS DOUBLE)" if has_bid_priceUsd else "NULL"
        bids_col = "TRY_CAST(tender_recordedbidscount AS DOUBLE)" if has_recorded_bids else "NULL"

        # Build a buyer-year aggregate first, then self-join with cumulative window
        # This avoids an O(n²) self-join
        print("       Building buyer-year aggregates...", flush=True)
        con.execute(f"""
            CREATE TABLE buyer_year_agg AS
            SELECT
                buyer_masterid,
                CAST(publish_year AS INTEGER) AS yr,
                COUNT(CASE WHEN did_win = 1 THEN 1 END) AS awards_in_year,
                AVG(CASE WHEN did_win = 1 THEN {price_col} END) AS avg_price_in_year,
                SUM(CASE WHEN did_win = 1 THEN {price_col} END) AS sum_price_in_year,
                AVG({bids_col}) AS avg_bids_in_year,
                COUNT(*) AS rows_in_year
            FROM joined
            GROUP BY buyer_masterid, CAST(publish_year AS INTEGER)
        """)

        bya_count = con.execute("SELECT COUNT(*) FROM buyer_year_agg").fetchone()[0]
        print(f"       Buyer-year aggregates: {bya_count:,} groups", flush=True)

        # Now compute cumulative stats up to (but NOT including) current year
        print("       Computing cumulative point-in-time features...", flush=True)
        con.execute("""
            CREATE TABLE buyer_history AS
            SELECT
                buyer_masterid,
                yr,
                -- Sum of all awards in years strictly before this year
                SUM(awards_in_year) OVER (
                    PARTITION BY buyer_masterid
                    ORDER BY yr
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ) AS buyer_total_past_awards,
                -- Weighted avg price: total price sum / total awards in prior years
                SUM(sum_price_in_year) OVER (
                    PARTITION BY buyer_masterid
                    ORDER BY yr
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ) AS _cum_price_sum,
                SUM(awards_in_year) OVER (
                    PARTITION BY buyer_masterid
                    ORDER BY yr
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ) AS _cum_awards,
                -- For avg competitors: weighted by rows_in_year
                SUM(avg_bids_in_year * rows_in_year) OVER (
                    PARTITION BY buyer_masterid
                    ORDER BY yr
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ) AS _cum_bids_weighted,
                SUM(rows_in_year) OVER (
                    PARTITION BY buyer_masterid
                    ORDER BY yr
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ) AS _cum_rows
            FROM buyer_year_agg
        """)

        # Derive final features
        con.execute("""
            ALTER TABLE buyer_history ADD COLUMN buyer_avg_contract_value DOUBLE;
        """)
        con.execute("""
            UPDATE buyer_history SET buyer_avg_contract_value =
                CASE WHEN _cum_awards > 0
                     THEN _cum_price_sum / _cum_awards
                     ELSE NULL END
        """)

        con.execute("""
            ALTER TABLE buyer_history ADD COLUMN buyer_avg_competitors DOUBLE;
        """)
        con.execute("""
            UPDATE buyer_history SET buyer_avg_competitors =
                CASE WHEN _cum_rows > 0
                     THEN _cum_bids_weighted / _cum_rows
                     ELSE NULL END
        """)

        # Join buyer history back to main table
        con.execute("""
            ALTER TABLE joined ADD COLUMN buyer_total_past_awards INTEGER;
        """)
        con.execute("""
            ALTER TABLE joined ADD COLUMN buyer_avg_contract_value DOUBLE;
        """)
        con.execute("""
            ALTER TABLE joined ADD COLUMN buyer_avg_competitors DOUBLE;
        """)

        con.execute("""
            UPDATE joined SET
                buyer_total_past_awards = COALESCE(bh.buyer_total_past_awards, 0),
                buyer_avg_contract_value = bh.buyer_avg_contract_value,
                buyer_avg_competitors = bh.buyer_avg_competitors
            FROM buyer_history bh
            WHERE joined.buyer_masterid = bh.buyer_masterid
              AND CAST(joined.publish_year AS INTEGER) = bh.yr
        """)

        print("       Building buyer-CPV aggregates...", flush=True)
        con.execute("""
            CREATE TABLE buyer_cpv_agg AS
            SELECT
                buyer_masterid,
                SUBSTRING(tender_cpvs, 1, 2) AS cpv_division,
                CAST(publish_year AS INTEGER) AS yr,
                COUNT(CASE WHEN did_win = 1 THEN 1 END) AS cpv_awards_in_year
            FROM joined
            GROUP BY buyer_masterid, SUBSTRING(tender_cpvs, 1, 2), CAST(publish_year AS INTEGER)
        """)

        con.execute("""
            CREATE TABLE buyer_cpv_history AS
            SELECT
                buyer_masterid,
                cpv_division,
                yr,
                SUM(cpv_awards_in_year) OVER (
                    PARTITION BY buyer_masterid, cpv_division
                    ORDER BY yr
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ) AS buyer_cpv_past_awards
            FROM buyer_cpv_agg
        """)

        con.execute("""
            ALTER TABLE joined ADD COLUMN buyer_specialisation DOUBLE;
        """)

        con.execute("""
            UPDATE joined SET
                buyer_specialisation = CASE
                    WHEN buyer_total_past_awards > 0 THEN COALESCE(bch.buyer_cpv_past_awards, 0)::DOUBLE / buyer_total_past_awards
                    ELSE 0.0
                END
            FROM buyer_cpv_history bch
            WHERE joined.buyer_masterid = bch.buyer_masterid
              AND SUBSTRING(joined.tender_cpvs, 1, 2) = bch.cpv_division
              AND CAST(joined.publish_year AS INTEGER) = bch.yr
        """)

        con.execute("DROP TABLE IF EXISTS buyer_cpv_agg")
        con.execute("DROP TABLE IF EXISTS buyer_cpv_history")

        # Stats on buyer history
        buyer_stats = con.execute("""
            SELECT
                AVG(buyer_total_past_awards) AS avg_past,
                MAX(buyer_total_past_awards) AS max_past,
                COUNT(CASE WHEN buyer_total_past_awards > 0 THEN 1 END) AS has_history,
                COUNT(*) AS total
            FROM joined
        """).fetchone()
        print(f"       Buyer past awards — avg: {buyer_stats[0]:.1f}, max: {buyer_stats[1]}", flush=True)
        print(f"       Rows with buyer history: {buyer_stats[2]:,} / {buyer_stats[3]:,} "
              f"({100*buyer_stats[2]/buyer_stats[3]:.1f}%)", flush=True)

        # Clean up temp tables
        con.execute("DROP TABLE IF EXISTS buyer_year_agg")
        con.execute("DROP TABLE IF EXISTS buyer_history")

    # ── 6. Time-based split ────────────────────────────────────────────────
    print("\n[5/7] Computing time-based train/val/test split...", flush=True)

    # Get sorted unique years with row counts
    year_dist = con.execute("""
        SELECT CAST(publish_year AS INTEGER) AS yr, COUNT(*) AS cnt
        FROM joined
        GROUP BY CAST(publish_year AS INTEGER)
        ORDER BY yr
    """).fetchall()

    total_rows = sum(r[1] for r in year_dist)
    print(f"       Total rows: {total_rows:,}", flush=True)
    print(f"       Year distribution:", flush=True)
    for yr, cnt in year_dist:
        pct = 100 * cnt / total_rows
        print(f"         {yr}: {cnt:>10,} rows ({pct:5.1f}%)", flush=True)

    # Strict year-based split: Train <= 2019, Val 2020, Test >= 2021
    train_end_year = 2019
    val_end_year = 2020

    print(f"       Applying strict time split:", flush=True)
    print(f"         Train: <= {train_end_year}", flush=True)
    print(f"         Val  : == {val_end_year}", flush=True)
    print(f"         Test : >= {val_end_year + 1}", flush=True)

    # Assign split column
    con.execute(f"""
        ALTER TABLE joined ADD COLUMN split VARCHAR;
    """)
    con.execute(f"""
        UPDATE joined SET split =
            CASE
                WHEN CAST(publish_year AS INTEGER) <= {train_end_year} THEN 'train'
                WHEN CAST(publish_year AS INTEGER) <= {val_end_year} THEN 'val'
                ELSE 'test'
            END
    """)

    # Print split stats
    split_stats = con.execute("""
        SELECT
            split,
            COUNT(*) AS cnt,
            MIN(CAST(publish_year AS INTEGER)) AS min_yr,
            MAX(CAST(publish_year AS INTEGER)) AS max_yr,
            SUM(CASE WHEN did_win = 1 THEN 1 ELSE 0 END) AS pos,
            SUM(CASE WHEN did_win = 0 THEN 1 ELSE 0 END) AS neg
        FROM joined
        GROUP BY split
        ORDER BY min_yr
    """).fetchall()

    print(f"\n       {'Split':<8} {'Rows':>12} {'%':>7} {'Years':>14} {'Pos':>10} {'Neg':>10} {'Pos%':>7}", flush=True)
    print(f"       {'─'*8} {'─'*12} {'─'*7} {'─'*14} {'─'*10} {'─'*10} {'─'*7}", flush=True)
    for row in split_stats:
        split_name, cnt, min_yr, max_yr, pos, neg = row
        pct = 100 * cnt / total_rows
        pos_pct = 100 * pos / cnt if cnt > 0 else 0
        print(f"       {split_name:<8} {cnt:>12,} {pct:>6.1f}% {min_yr}–{max_yr:>8} "
              f"{pos:>10,} {neg:>10,} {pos_pct:>6.1f}%", flush=True)

    # ── 7. Leakage check ──────────────────────────────────────────────────
    print("\n[6/7] Running leakage check...", flush=True)

    leakage = con.execute("""
        SELECT COUNT(*) FROM joined
        WHERE split = 'test'
          AND CAST(publish_year AS INTEGER) < (
              SELECT MAX(CAST(publish_year AS INTEGER))
              FROM joined WHERE split = 'train'
          )
    """).fetchone()[0]

    leakage_val = con.execute("""
        SELECT COUNT(*) FROM joined
        WHERE split = 'val'
          AND CAST(publish_year AS INTEGER) < (
              SELECT MAX(CAST(publish_year AS INTEGER))
              FROM joined WHERE split = 'train'
          )
    """).fetchone()[0]

    # Also check test vs val
    leakage_test_val = con.execute("""
        SELECT COUNT(*) FROM joined
        WHERE split = 'test'
          AND CAST(publish_year AS INTEGER) < (
              SELECT MAX(CAST(publish_year AS INTEGER))
              FROM joined WHERE split = 'val'
          )
    """).fetchone()[0]

    # Get min/max years per split for reporting
    for row in split_stats:
        split_name, cnt, min_yr, max_yr, _, _ = row
        print(f"       {split_name}: publish_year range [{min_yr}, {max_yr}]", flush=True)

    if leakage > 0 or leakage_val > 0 or leakage_test_val > 0:
        print(f"\n       ❌ LEAKAGE DETECTED!", flush=True)
        print(f"          Test rows before train max year: {leakage}", flush=True)
        print(f"          Val rows before train max year: {leakage_val}", flush=True)
        print(f"          Test rows before val max year: {leakage_test_val}", flush=True)
        raise RuntimeError("LEAKAGE CHECK FAILED — temporal ordering violated!")
    else:
        print(f"       ✓ LEAKAGE CHECK PASSED — no future data leaks into training", flush=True)

    # ── 8. Save outputs ───────────────────────────────────────────────────
    print("\n[7/7] Saving master training dataset...", flush=True)

    # Get final column list
    final_cols = [r[0] for r in con.execute("DESCRIBE joined").fetchall()]
    print(f"       Final columns ({len(final_cols)}): {final_cols}", flush=True)

    # Save to parquet
    out_path = str(OUTPUT_PATH).replace("'", "''")
    con.execute(f"COPY joined TO '{out_path}' (FORMAT 'parquet', COMPRESSION 'zstd')")
    parquet_size = OUTPUT_PATH.stat().st_size / (1024 * 1024)
    print(f"       Parquet saved: {OUTPUT_PATH.name} ({parquet_size:.1f} MB)", flush=True)

    # Save to SQLite
    total_rows_to_save = min(total_rows, 1000000)
    print(f"       Saving to SQLite (capped at {total_rows_to_save:,} rows to prevent disk space exhaustion)...", flush=True)
    conn_sqlite = sqlite3.connect(str(DB_PATH))

    # Drop existing table first
    conn_sqlite.execute("DROP TABLE IF EXISTS master_training_dataset")
    conn_sqlite.commit()

    # Export in chunks to keep RAM low
    CHUNK_SIZE = 100_000
    offset = 0
    first_chunk = True

    while offset < total_rows_to_save:
        chunk_df = con.execute(f"""
            SELECT * FROM joined
            LIMIT {min(CHUNK_SIZE, total_rows_to_save - offset)} OFFSET {offset}
        """).fetchdf()

        if len(chunk_df) == 0:
            break

        chunk_df.to_sql(
            "master_training_dataset",
            conn_sqlite,
            if_exists='append' if not first_chunk else 'replace',
            index=False
        )
        offset += CHUNK_SIZE
        first_chunk = False
        print(f"         Written {min(offset, total_rows_to_save):,} / {total_rows_to_save:,} rows...", flush=True)

    conn_sqlite.close()
    print(f"  → SQLite: master_training_dataset table saved to {DB_PATH} ({total_rows_to_save:,} rows)", flush=True)

    # ── 9. Save metadata JSON ─────────────────────────────────────────────
    print("\n       Saving dataset metadata...", flush=True)

    # Gather stats
    label_stats = con.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN did_win = 1 THEN 1 ELSE 0 END) AS pos,
            SUM(CASE WHEN did_win = 0 THEN 1 ELSE 0 END) AS neg
        FROM joined
    """).fetchone()
    total, pos, neg = label_stats

    # scale_pos_weight for imbalanced classification (neg / pos)
    scale_pos_weight = neg / pos if pos > 0 else 1.0

    # Date ranges per split
    split_ranges = {}
    for row in split_stats:
        split_name, cnt, min_yr, max_yr, _, _ = row
        split_ranges[split_name] = {"min_year": int(min_yr), "max_year": int(max_yr), "rows": int(cnt)}

    # Countries
    countries = []
    if "buyer_country" in final_cols:
        countries_result = con.execute("""
            SELECT DISTINCT buyer_country
            FROM joined
            WHERE buyer_country IS NOT NULL
            ORDER BY buyer_country
        """).fetchall()
        countries = [r[0] for r in countries_result]

    # Feature count: all columns minus identifiers and target
    non_feature_cols = {
        "pair_id", "tender_id", "lot_id", "bid_id", "persistent_id",
        "bidder_masterid", "bidder_name", "buyer_masterid", "buyer_name",
        "did_win", "split"
    }
    feature_cols = [c for c in final_cols if c not in non_feature_cols]

    metadata = {
        "total_rows": int(total),
        "positive_examples": int(pos),
        "negative_examples": int(neg),
        "scale_pos_weight": round(scale_pos_weight, 4),
        "train_date_range": split_ranges.get("train", {}),
        "val_date_range": split_ranges.get("val", {}),
        "test_date_range": split_ranges.get("test", {}),
        "feature_count": len(feature_cols),
        "feature_columns": feature_cols,
        "countries": countries,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_files": {
            "labeled_pairs": str(LABELED_PATH),
            "supplier_features": str(SUPPLIER_PATH),
        },
    }

    with open(METADATA_PATH, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"       Metadata saved: {METADATA_PATH.name}", flush=True)

    con.close()

    # ── Completion summary ─────────────────────────────────────────────────
    elapsed = time.time() - t_start
    print("\n" + "=" * 72, flush=True)
    print("  05_build_dataset — COMPLETE", flush=True)
    print("=" * 72, flush=True)
    print(f"  Total rows:        {total:>12,}", flush=True)
    print(f"  Positive (win):    {pos:>12,}  ({100*pos/total:.1f}%)", flush=True)
    print(f"  Negative (lose):   {neg:>12,}  ({100*neg/total:.1f}%)", flush=True)
    print(f"  scale_pos_weight:  {scale_pos_weight:>12.4f}", flush=True)
    print(f"  Features:          {len(feature_cols):>12}", flush=True)
    print(f"  Countries:         {countries}", flush=True)
    print(f"  Parquet:           {OUTPUT_PATH}", flush=True)
    print(f"  SQLite:            {DB_PATH} → master_training_dataset", flush=True)
    print(f"  Metadata:          {METADATA_PATH}", flush=True)
    print(f"  Elapsed:           {elapsed:.1f}s", flush=True)
    print("=" * 72, flush=True)


if __name__ == "__main__":
    build_dataset()
