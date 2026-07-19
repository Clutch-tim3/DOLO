#!/usr/bin/env python3
"""
04_supplier_features.py — Compute point-in-time supplier history features.

CRITICAL TEMPORAL RULE:
  For a tender in year Y, supplier features use ONLY contracts from years < Y.
  This prevents data leakage and ensures point-in-time correctness.
"""

import sys
import time
import sqlite3
from pathlib import Path
import duckdb

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DB_PATH = PROJECT_ROOT / "data" / "procurement.db"

LABELED_PAIRS_PATH = PROCESSED_DIR / "labeled_pairs.parquet"
RAW_CONTRACTS_PATH = PROCESSED_DIR / "raw_contracts.parquet"
OUTPUT_PATH = PROCESSED_DIR / "supplier_features.parquet"

# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════
def main():
    t0 = time.time()
    print("=" * 70, flush=True)
    print("  04_supplier_features.py — Point-in-time supplier features (DuckDB SQL)", flush=True)
    print("=" * 70, flush=True)

    # ── Validate inputs ───────────────────────────────────────────────
    if not LABELED_PAIRS_PATH.exists():
        print(f"ERROR: {LABELED_PAIRS_PATH} not found. Run 03_build_pairs first.", flush=True)
        sys.exit(1)
    if not RAW_CONTRACTS_PATH.exists():
        print(f"ERROR: {RAW_CONTRACTS_PATH} not found. Run 02_ingest first.", flush=True)
        sys.exit(1)

    # ── Initialise DuckDB    # Setup DuckDB connection with strict memory limits but large spill-to-disk allowance
    con = duckdb.connect(database=':memory:')
    con.execute("PRAGMA threads=4;")
    con.execute("PRAGMA memory_limit='4GB';")
    con.execute("PRAGMA max_temp_directory_size='50GiB';")

    raw_contracts_sql = str(RAW_CONTRACTS_PATH).replace("'", "''")
    labeled_pairs_sql = str(LABELED_PAIRS_PATH).replace("'", "''")
    output_sql = str(OUTPUT_PATH).replace("'", "''")

    print("\n[STEP 1] Generating and saving all features via DuckDB SQL...", flush=True)
    
    con.execute(f"""
        COPY (
            WITH raw_contracts_grouped AS (
                SELECT
                    COALESCE(bidder_name, 'UNKNOWN') as bidder_key,
                    tender_year::INTEGER as tender_year,
                    COUNT(*)::INTEGER as year_entries,
                    SUM(CASE WHEN bid_iswinning = TRUE THEN 1 ELSE 0 END)::INTEGER as year_wins,
                    SUM(CASE WHEN bid_iswinning = TRUE AND bid_priceUsd IS NOT NULL THEN bid_priceUsd ELSE 0.0 END)::DOUBLE as year_value_sum,
                    COUNT(CASE WHEN bid_iswinning = TRUE AND bid_priceUsd IS NOT NULL THEN 1 END)::INTEGER as year_value_count,
                    MAX(CASE WHEN bid_iswinning = TRUE THEN tender_year END)::INTEGER as year_max_win_year
                FROM read_parquet('{raw_contracts_sql}')
                WHERE tender_year IS NOT NULL
                GROUP BY bidder_key, tender_year
            ),
            supplier_year_stats AS (
                SELECT
                    bidder_key,
                    tender_year,
                    COALESCE(SUM(year_entries) OVER (
                        PARTITION BY bidder_key ORDER BY tender_year 
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ), 0)::INTEGER as cum_entries,
                    COALESCE(SUM(year_wins) OVER (
                        PARTITION BY bidder_key ORDER BY tender_year 
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ), 0)::INTEGER as cum_wins,
                    COALESCE(SUM(year_value_sum) OVER (
                        PARTITION BY bidder_key ORDER BY tender_year 
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ), 0.0)::DOUBLE as cum_value_sum,
                    COALESCE(SUM(year_value_count) OVER (
                        PARTITION BY bidder_key ORDER BY tender_year 
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ), 0)::INTEGER as cum_value_count,
                    MAX(year_max_win_year) OVER (
                        PARTITION BY bidder_key ORDER BY tender_year 
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    )::INTEGER as last_win_year
                FROM raw_contracts_grouped
            ),
            raw_country_grouped AS (
                SELECT
                    COALESCE(bidder_name, 'UNKNOWN') as bidder_key,
                    buyer_country,
                    tender_year::INTEGER as tender_year,
                    COUNT(*)::INTEGER as year_entries,
                    SUM(CASE WHEN bid_iswinning = TRUE THEN 1 ELSE 0 END)::INTEGER as year_wins
                FROM read_parquet('{raw_contracts_sql}')
                WHERE tender_year IS NOT NULL AND buyer_country IS NOT NULL
                GROUP BY bidder_key, buyer_country, tender_year
            ),
            supplier_country_stats AS (
                SELECT
                    bidder_key,
                    buyer_country,
                    tender_year,
                    COALESCE(SUM(year_entries) OVER (
                        PARTITION BY bidder_key, buyer_country ORDER BY tender_year 
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ), 0)::INTEGER as cum_entries_country,
                    COALESCE(SUM(year_wins) OVER (
                        PARTITION BY bidder_key, buyer_country ORDER BY tender_year 
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ), 0)::INTEGER as cum_wins_country
                FROM raw_country_grouped
            ),
            country_counts AS (
                SELECT
                    sy.bidder_key,
                    sy.tender_year,
                    COUNT(CASE WHEN scm.min_year <= sy.tender_year THEN 1 END)::INTEGER as pit_country_count
                FROM (
                    SELECT DISTINCT COALESCE(bidder_name, 'UNKNOWN') as bidder_key, tender_year::INTEGER as tender_year 
                    FROM read_parquet('{raw_contracts_sql}')
                    WHERE tender_year IS NOT NULL
                ) sy
                LEFT JOIN (
                    SELECT COALESCE(bidder_name, 'UNKNOWN') as bidder_key, buyer_country, MIN(tender_year::INTEGER) as min_year
                    FROM read_parquet('{raw_contracts_sql}')
                    WHERE tender_year IS NOT NULL AND buyer_country IS NOT NULL
                    GROUP BY bidder_key, buyer_country
                ) scm
                  ON sy.bidder_key = scm.bidder_key
                GROUP BY sy.bidder_key, sy.tender_year

            ),
            raw_buyer_grouped AS (
                SELECT
                    COALESCE(bidder_name, 'UNKNOWN') as bidder_key,
                    buyer_masterid,
                    tender_year::INTEGER as tender_year,
                    COUNT(*)::INTEGER as year_entries,
                    SUM(CASE WHEN bid_iswinning = TRUE THEN 1 ELSE 0 END)::INTEGER as year_wins
                FROM read_parquet('{raw_contracts_sql}')
                WHERE tender_year IS NOT NULL AND buyer_masterid IS NOT NULL
                GROUP BY bidder_key, buyer_masterid, tender_year
            ),
            supplier_buyer_stats AS (
                SELECT
                    bidder_key,
                    buyer_masterid,
                    tender_year,
                    COALESCE(SUM(year_entries) OVER (
                        PARTITION BY bidder_key, buyer_masterid ORDER BY tender_year 
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ), 0)::INTEGER as cum_entries_buyer,
                    COALESCE(SUM(year_wins) OVER (
                        PARTITION BY bidder_key, buyer_masterid ORDER BY tender_year 
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ), 0)::INTEGER as cum_wins_buyer
                FROM raw_buyer_grouped
            ),
            supplier_hhi_prep AS (
                SELECT 
                    COALESCE(bidder_name, 'UNKNOWN') as bidder_key,
                    tender_year::INTEGER as win_year,
                    SUBSTRING(tender_cpvs, 1, 2) as cpv_division
                FROM read_parquet('{raw_contracts_sql}')
                WHERE bid_iswinning = TRUE AND tender_cpvs IS NOT NULL AND tender_year IS NOT NULL
            ),
            supplier_hhi_counts AS (
                SELECT 
                    p.bidder_key,
                    p.tender_year,
                    h.cpv_division,
                    COUNT(*) as cpv_wins
                FROM (SELECT DISTINCT COALESCE(bidder_name, 'UNKNOWN') as bidder_key, publish_year::INTEGER as tender_year FROM read_parquet('{labeled_pairs_sql}')) p
                JOIN supplier_hhi_prep h
                  ON p.bidder_key = h.bidder_key AND h.win_year < p.tender_year
                GROUP BY p.bidder_key, p.tender_year, h.cpv_division
            ),
            supplier_hhi_totals AS (
                SELECT 
                    bidder_key,
                    tender_year,
                    cpv_wins,
                    SUM(cpv_wins) OVER (PARTITION BY bidder_key, tender_year) as total_wins
                FROM supplier_hhi_counts
            ),
            supplier_hhi AS (
                SELECT 
                    bidder_key,
                    tender_year,
                    SUM(POW(cpv_wins::DOUBLE / NULLIF(total_wins, 0), 2)) as category_hhi
                FROM supplier_hhi_totals
                GROUP BY bidder_key, tender_year
            ),
            joined AS (
                SELECT
                    p.pair_id,
                    COALESCE(sy.cum_entries, 0)::INTEGER as pit_total_entries,
                    COALESCE(sy.cum_wins, 0)::INTEGER as pit_total_wins,
                    CASE 
                        WHEN COALESCE(sy.cum_entries, 0) > 0 
                        THEN COALESCE(sy.cum_wins, 0)::DOUBLE / sy.cum_entries 
                        ELSE 0.0 
                    END as pit_win_rate_overall,
                    CASE 
                        WHEN COALESCE(sc.cum_entries_country, 0) > 0 
                        THEN COALESCE(sc.cum_wins_country, 0)::DOUBLE / sc.cum_entries_country 
                        ELSE 0.0 
                    END as pit_win_rate_country,
                    CASE 
                        WHEN COALESCE(sy.cum_value_count, 0) > 0 
                        THEN sy.cum_value_sum / sy.cum_value_count 
                        ELSE 0.0 
                    END as pit_avg_contract_value,
                    CASE 
                        WHEN sy.last_win_year IS NOT NULL 
                        THEN (p.publish_year::INTEGER - sy.last_win_year)::DOUBLE 
                        ELSE 10.0 
                    END as last_win_years_ago_raw,
                    COALESCE(cc.pit_country_count, 0)::INTEGER as pit_country_count,
                    COALESCE(sb.cum_entries_buyer, 0)::INTEGER as pit_buyer_entry_count,
                    COALESCE(sb.cum_wins_buyer, 0)::INTEGER as pit_buyer_win_count,
                    CASE 
                        WHEN COALESCE(sy.cum_wins, 0) > 0 
                        THEN COALESCE(sb.cum_wins_buyer, 0)::DOUBLE / sy.cum_wins 
                        ELSE 0.0 
                    END as buyer_loyalty_score,
                    COALESCE(sh.category_hhi, 0.0)::DOUBLE as category_hhi
                FROM (SELECT *, COALESCE(bidder_name, 'UNKNOWN') as bidder_key FROM read_parquet('{labeled_pairs_sql}')) p
                ASOF LEFT JOIN supplier_year_stats sy
                  ON p.bidder_key = sy.bidder_key AND p.publish_year::INTEGER - 1 >= sy.tender_year
                ASOF LEFT JOIN supplier_country_stats sc
                  ON p.bidder_key = sc.bidder_key AND p.buyer_country = sc.buyer_country AND p.publish_year::INTEGER - 1 >= sc.tender_year
                ASOF LEFT JOIN country_counts cc
                  ON p.bidder_key = cc.bidder_key AND p.publish_year::INTEGER - 1 >= cc.tender_year
                ASOF LEFT JOIN supplier_buyer_stats sb
                  ON p.bidder_key = sb.bidder_key AND p.buyer_masterid = sb.buyer_masterid AND p.publish_year::INTEGER - 1 >= sb.tender_year
                LEFT JOIN supplier_hhi sh
                  ON p.bidder_key = sh.bidder_key AND p.publish_year::INTEGER = sh.tender_year
            )
            SELECT
                pair_id,
                pit_total_entries,
                pit_total_wins,
                pit_win_rate_overall,
                pit_win_rate_country,
                pit_avg_contract_value,
                CASE WHEN last_win_years_ago_raw < 0 THEN 0.0 ELSE last_win_years_ago_raw END as pit_last_win_years_ago,
                LN(1.0 + pit_total_wins) as pit_experience_score,
                1.0 / (1.0 + CASE WHEN last_win_years_ago_raw < 0 THEN 0.0 ELSE last_win_years_ago_raw END) as pit_recency_score,
                CASE WHEN pit_buyer_win_count > 0 THEN 1 ELSE 0 END as pit_is_incumbent,
                pit_country_count,
                CASE WHEN pit_country_count > 1 THEN 1 ELSE 0 END as pit_is_international,
                pit_buyer_win_count,
                pit_buyer_entry_count,
                CASE 
                    WHEN pit_buyer_entry_count > 0 
                    THEN pit_buyer_win_count::DOUBLE / pit_buyer_entry_count 
                    ELSE 0.0 
                END as pit_win_rate_buyer,
                buyer_loyalty_score,
                category_hhi
            FROM joined
        ) TO '{output_sql}' (FORMAT PARQUET, COMPRESSION ZSTD);
    """)

    print(f"  → Saved to Parquet: {OUTPUT_PATH}", flush=True)

    # ── Step 2: Save to SQLite ────────────────────────────────────────
    print("\n[STEP 2] Saving to SQLite (capped at 1,000,000 rows)...", flush=True)
    
    # Clean table
    sqlite_conn = sqlite3.connect(str(DB_PATH))
    sqlite_conn.execute("DROP TABLE IF EXISTS supplier_features;")
    sqlite_conn.commit()
    sqlite_conn.close()

    # Load extension and copy
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{str(DB_PATH)}' AS sqlite_db (TYPE SQLITE);")
    con.execute(f"""
        CREATE TABLE sqlite_db.supplier_features AS
        SELECT * FROM read_parquet('{output_sql}')
        LIMIT 1000000;
    """)
    con.execute("DETACH sqlite_db;")

    con.close()

    elapsed = time.time() - t0
    print(f"\n{'=' * 70}", flush=True)
    print(f"  ✅ COMPLETE — 04_supplier_features.py", flush=True)
    print(f"  Saved to    : {OUTPUT_PATH}", flush=True)
    print(f"  SQLite table: supplier_features @ {DB_PATH} (1,000,000 rows)", flush=True)
    print(f"  Elapsed     : {elapsed:.1f}s", flush=True)
    print(f"{'=' * 70}", flush=True)


if __name__ == "__main__":
    main()
