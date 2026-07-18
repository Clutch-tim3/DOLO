#!/usr/bin/env python3
"""
02_ingest.py — Extract relevant columns from raw GPPD CSV.GZ files.

Reads IT_DIB_2023.csv.gz and US_DIB_2023.csv.gz via DuckDB,
selects the columns needed for the tender prediction pipeline,
adds a source_country tag, and exports to Parquet + SQLite.

Usage:
    python pipeline/02_ingest.py
"""

import os
import sys
import time
import sqlite3
import duckdb

# ── paths ────────────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(
    os.path.dirname(PROJECT_ROOT),
    "data", "raw",
    "GTI Global Public Procurement Dataset (GPPD) 22",
)
IT_FILE = os.path.join(RAW_DIR, "IT_DIB_2023.csv.gz")
US_FILE = os.path.join(RAW_DIR, "US_DIB_2023.csv.gz")

OUT_DIR = os.path.join(PROJECT_ROOT, "data", "processed")
OUT_PARQUET = os.path.join(OUT_DIR, "raw_contracts.parquet")
SQLITE_DB = os.path.join(PROJECT_ROOT, "data", "procurement.db")

# ── columns to extract ───────────────────────────────────────────────────────
COLUMNS = [
    "persistent_id", "tender_id", "lot_id", "bid_id",
    "buyer_masterid", "buyer_name", "buyer_country", "buyer_buyertype",
    "bidder_masterid", "bidder_name", "bidder_country",
    "bid_iswinning",
    "tender_proceduretype", "tender_supplytype", "tender_cpvs",
    "bid_priceUsd", "tender_digiwhist_price", "bid_digiwhist_price",
    "tender_estimatedpriceUsd", "tender_finalpriceUsd",
    "tender_year",
    "tender_publications_firstcallfortenderdate",
    "tender_awarddecisiondate", "tender_biddeadline",
    "tender_recordedbidscount", "lot_bidscount",
    "tender_lotscount", "tender_awardcriteria_count",
    "tender_selectionmethod",
    "tender_description_length", "lot_description_length",
    "tender_personalrequirements_length",
    "tender_technicalrequirements_length",
    "tender_economicrequirements_length",
    "submission_period",
    "corr_singleb", "corr_proc", "corr_subm",
    "corr_buyer_concentration", "cri",
    "currency", "filter_ok", "filter_losingbids", "filter_cancelled",
    "lot_updateddurationdays", "tender_corrections_count",
    "lot_status",
]

COL_LIST = ",\n        ".join(COLUMNS)


def main():
    t0 = time.time()

    # ── validate raw files ───────────────────────────────────────────────
    for path in (IT_FILE, US_FILE):
        if not os.path.isfile(path):
            print(f"ERROR: raw file not found: {path}", flush=True)
            sys.exit(1)
    print("✓ Raw files located", flush=True)

    # ── ensure output dirs ───────────────────────────────────────────────
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(os.path.join(PROJECT_ROOT, "data", "tmp"), exist_ok=True)
    os.makedirs(os.path.dirname(SQLITE_DB), exist_ok=True)

    # ── DuckDB: extract + union + export ─────────────────────────────────
    con = duckdb.connect(database=":memory:")
    con.execute("SET temp_directory='data/tmp';")
    con.execute("SET max_temp_directory_size='20GB';")
    con.execute("SET preserve_insertion_order=false;")
    con.execute("SET memory_limit='400MB';")
    con.execute("SET threads=2;")
    print("✓ DuckDB initialised (memory_limit=400MB, threads=2, temp_dir=data/tmp)", flush=True)

    IT_TEMP_PARQUET = os.path.join(OUT_DIR, "raw_contracts_it.parquet")
    US_TEMP_PARQUET = os.path.join(OUT_DIR, "raw_contracts_us.parquet")

    # Ingest IT file first
    if not os.path.exists(IT_TEMP_PARQUET):
        print("  Ingesting IT gzipped CSV to temp Parquet...", flush=True)
        it_query = f"""
        COPY (
            SELECT {COL_LIST}, 'IT' AS source_country
            FROM read_csv_auto('{IT_FILE}', header=true, sample_size=100000)
            WHERE bid_iswinning IS NOT NULL
        ) TO '{IT_TEMP_PARQUET}' (FORMAT PARQUET, COMPRESSION ZSTD);
        """
        con.execute(it_query)
        print("  ✓ IT temp Parquet saved.", flush=True)
    else:
        print("  ✓ IT temp Parquet already exists. Skipping ingestion.", flush=True)

    # Ingest US file second
    if not os.path.exists(US_TEMP_PARQUET):
        print("  Ingesting US gzipped CSV to temp Parquet...", flush=True)
        us_query = f"""
        COPY (
            SELECT {COL_LIST}, 'US' AS source_country
            FROM read_csv_auto('{US_FILE}', header=true, sample_size=100000)
            WHERE bid_iswinning IS NOT NULL
        ) TO '{US_TEMP_PARQUET}' (FORMAT PARQUET, COMPRESSION ZSTD);
        """
        con.execute(us_query)
        print("  ✓ US temp Parquet saved.", flush=True)
    else:
        print("  ✓ US temp Parquet already exists. Skipping ingestion.", flush=True)

    # Union the two Parquets
    print("  Unioning temp Parquet files to final Parquet...", flush=True)
    con.execute("SET memory_limit='4GB';")
    con.execute("SET threads=4;")
    union_query = f"""
    COPY (
        SELECT * FROM read_parquet('{IT_TEMP_PARQUET}')
        UNION ALL
        SELECT * FROM read_parquet('{US_TEMP_PARQUET}')
    ) TO '{OUT_PARQUET}' (FORMAT PARQUET, COMPRESSION ZSTD);
    """
    con.execute(union_query)

    # Clean up temp files
    if os.path.exists(IT_TEMP_PARQUET):
        os.remove(IT_TEMP_PARQUET)
    if os.path.exists(US_TEMP_PARQUET):
        os.remove(US_TEMP_PARQUET)

    parquet_size_mb = os.path.getsize(OUT_PARQUET) / (1024 * 1024)
    print(f"✓ Final Parquet saved: {OUT_PARQUET} ({parquet_size_mb:.1f} MB)", flush=True)

    # ── summary stats (read from the parquet — fast) ─────────────────────
    print("\n── Summary statistics ──────────────────────────────", flush=True)

    total_rows = con.execute(
        f"SELECT COUNT(*) FROM '{OUT_PARQUET}'"
    ).fetchone()[0]
    print(f"  Total rows : {total_rows:,}", flush=True)

    ncols = con.execute(
        f"SELECT COUNT(*) FROM (DESCRIBE SELECT * FROM '{OUT_PARQUET}')"
    ).fetchone()[0]
    print(f"  Columns    : {ncols}", flush=True)

    # Year range
    year_stats = con.execute(f"""
        SELECT MIN(tender_year), MAX(tender_year)
        FROM '{OUT_PARQUET}'
    """).fetchone()
    print(f"  Year range : {int(year_stats[0])} – {int(year_stats[1])}", flush=True)

    # Country breakdown
    country_rows = con.execute(f"""
        SELECT source_country, COUNT(*) AS n
        FROM '{OUT_PARQUET}'
        GROUP BY source_country
        ORDER BY source_country
    """).fetchall()
    for country, n in country_rows:
        print(f"  {country} rows   : {n:,}", flush=True)

    # Label distribution
    label_rows = con.execute(f"""
        SELECT bid_iswinning, COUNT(*) AS n
        FROM '{OUT_PARQUET}'
        GROUP BY bid_iswinning
        ORDER BY bid_iswinning
    """).fetchall()
    print("  bid_iswinning distribution:", flush=True)
    for val, n in label_rows:
        pct = 100.0 * n / total_rows
        print(f"    {val} : {n:,}  ({pct:.1f}%)", flush=True)

    # Null rates for key columns
    null_check_cols = [
        "tender_id", "bidder_masterid", "buyer_masterid",
        "bid_iswinning", "tender_year", "bid_priceUsd",
        "tender_proceduretype", "tender_cpvs",
        "tender_publications_firstcallfortenderdate",
        "tender_awarddecisiondate",
    ]
    print("  Null rates (key columns):", flush=True)
    for col in null_check_cols:
        null_n = con.execute(f"""
            SELECT COUNT(*) - COUNT("{col}")
            FROM '{OUT_PARQUET}'
        """).fetchone()[0]
        null_pct = 100.0 * null_n / total_rows
        print(f"    {col:50s} {null_pct:6.1f}%", flush=True)

    # ── SQLite: write directly via DuckDB SQLite Extension ────────────────
    print("\n  Saving to SQLite via DuckDB SQLite Extension …", flush=True)
    print("  (Note: Capping SQLite table at 1,000,000 rows to prevent disk space exhaustion)", flush=True)
    
    # Clear existing SQLite table using standard python library
    sqlite_conn = sqlite3.connect(SQLITE_DB)
    sqlite_conn.execute("DROP TABLE IF EXISTS raw_contracts;")
    sqlite_conn.commit()
    sqlite_conn.close()

    # Load extension and save
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{SQLITE_DB}' AS sqlite_db (TYPE SQLITE);")
    con.execute(f"""
        CREATE TABLE sqlite_db.raw_contracts AS
        SELECT * FROM '{OUT_PARQUET}'
        LIMIT 1000000;
    """)
    con.execute("DETACH sqlite_db;")

    sqlite_size_mb = os.path.getsize(SQLITE_DB) / (1024 * 1024)
    print(f"  → SQLite: raw_contracts table saved to {SQLITE_DB}  ({sqlite_size_mb:.1f} MB)", flush=True)

    con.close()

    elapsed = time.time() - t0
    print(f"\n{'='*60}", flush=True)
    print(f"02_ingest.py COMPLETE  |  {total_rows:,} rows  |  {elapsed:.0f}s", flush=True)
    print(f"  Parquet : {OUT_PARQUET}", flush=True)
    print(f"  SQLite  : {SQLITE_DB} (table: raw_contracts)", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
