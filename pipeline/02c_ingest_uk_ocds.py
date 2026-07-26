#!/usr/bin/env python3
"""
02c_ingest_uk_ocds.py — Ingest full UK Contracts Finder OCDS dataset into the unified GPPD schema.
Reads main.csv, awards.csv, and awards_suppliers.csv from data/uk_raw/full/ and performs DuckDB relational join.
"""

import sys
import time
from pathlib import Path
import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
RAW_CONTRACTS_PATH = PROCESSED_DIR / "raw_contracts.parquet"
UK_RAW_DIR = PROJECT_ROOT / "data" / "uk_raw" / "full"

def main():
    t0 = time.time()
    print("=" * 70, flush=True)
    print("  02c_ingest_uk_ocds.py — UK Contracts Finder Data Ingestion", flush=True)
    print("=" * 70, flush=True)

    main_csv = UK_RAW_DIR / "main.csv"
    awards_csv = UK_RAW_DIR / "awards.csv"
    suppliers_csv = UK_RAW_DIR / "awards_suppliers.csv"

    for p in [main_csv, awards_csv, suppliers_csv]:
        if not p.exists():
            print(f"ERROR: Required UK OCDS file missing: {p}", flush=True)
            sys.exit(1)

    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit = '6GB';")
    con.execute("SET threads = 4;")

    print("\n[1/3] Reading & relationally joining UK Contracts Finder CSVs...", flush=True)

    con.execute(f"""
        CREATE TABLE uk_mapped AS 
        SELECT 
            m.ocid as persistent_id,
            COALESCE(m.tender_id, m.ocid) as tender_id,
            CAST(a.id AS VARCHAR) as lot_id,
            CAST(s.id AS VARCHAR) as bid_id,
            COALESCE(CAST(m.buyer_id AS VARCHAR), 'UK_BUYER_' || MD5(COALESCE(m.buyer_name, 'UNKNOWN'))) as buyer_masterid,
            COALESCE(m.buyer_name, 'UK Public Authority') as buyer_name,
            'GB' as buyer_country,
            'NATIONAL_AUTHORITY' as buyer_buyertype,
            COALESCE(s.name, 'UK Supplier') as bidder_masterid,
            COALESCE(s.name, 'UK Supplier') as bidder_name,
            'GB' as bidder_country,
            True as bid_iswinning,
            COALESCE(m.tender_procurementMethod, 'OPEN') as tender_proceduretype,
            COALESCE(m.tender_mainProcurementCategory, 'SUPPLIES') as tender_supplytype,
            COALESCE(m.tender_classification_id, 'UNKNOWN') as tender_cpvs,
            TRY_CAST(a.value_amount AS DOUBLE) as bid_priceUsd,
            TRY_CAST(a.value_amount AS DOUBLE) as tender_digiwhist_price,
            TRY_CAST(a.value_amount AS DOUBLE) as bid_digiwhist_price,
            TRY_CAST(m.tender_value_amount AS DOUBLE) as tender_estimatedpriceUsd,
            TRY_CAST(a.value_amount AS DOUBLE) as tender_finalpriceUsd,
            EXTRACT(YEAR FROM TRY_CAST(m.date AS TIMESTAMP)) as tender_year,
            TRY_CAST(m.date AS TIMESTAMP) as tender_publications_firstcallfortenderdate,
            TRY_CAST(a.date AS TIMESTAMP) as tender_awarddecisiondate,
            TRY_CAST(m.tender_tenderPeriod_endDate AS TIMESTAMP) as tender_biddeadline,
            1 as tender_recordedbidscount,
            1 as lot_bidscount,
            1 as tender_lotscount,
            1 as tender_awardcriteria_count,
            'MEAT' as tender_selectionmethod,
            LENGTH(COALESCE(m.tender_description, '')) as tender_description_length,
            LENGTH(COALESCE(a.description, '')) as lot_description_length,
            0 as tender_personalrequirements_length,
            0 as tender_technicalrequirements_length,
            0 as tender_economicrequirements_length,
            CASE 
                WHEN m.date IS NOT NULL AND m.tender_tenderPeriod_endDate IS NOT NULL 
                THEN DATE_DIFF('day', TRY_CAST(m.date AS DATE), TRY_CAST(m.tender_tenderPeriod_endDate AS DATE))
                ELSE NULL 
            END as submission_period,
            1.0 as corr_singleb,
            0.0 as corr_proc,
            0.0 as corr_subm,
            0.0 as corr_buyer_concentration,
            0.0 as cri,
            COALESCE(m.tender_value_currency, 'GBP') as currency,
            True as filter_ok,
            False as filter_losingbids,
            False as filter_cancelled,
            NULL as lot_updateddurationdays,
            0 as tender_corrections_count,
            'AWARDED' as lot_status,
            'GB' as source_country
        FROM read_csv_auto('{main_csv}', ignore_errors=true) m
        INNER JOIN read_csv_auto('{awards_csv}', ignore_errors=true) a
            ON m.ocid = a.main_ocid
        INNER JOIN read_csv_auto('{suppliers_csv}', ignore_errors=true) s
            ON a.main_ocid = s.main_ocid AND a.id = s.awards_id
    """)

    uk_count = con.execute("SELECT COUNT(*) FROM uk_mapped").fetchone()[0]
    print(f"  -> Extracted {uk_count:,} valid UK relational award pairs", flush=True)

    if RAW_CONTRACTS_PATH.exists():
        print("\n[2/3] Appending to existing raw_contracts.parquet...", flush=True)
        con.execute(f"""
            CREATE TABLE combined AS 
            SELECT * FROM read_parquet('{RAW_CONTRACTS_PATH}')
            UNION ALL CORRESPONDING
            SELECT * FROM uk_mapped
        """)
    else:
        print("\n[2/3] Initializing raw_contracts.parquet with UK mapped table...", flush=True)
        con.execute("CREATE TABLE combined AS SELECT * FROM uk_mapped")

    total_count = con.execute("SELECT COUNT(*) FROM combined").fetchone()[0]
    print(f"  -> Combined raw_contracts count: {total_count:,}", flush=True)

    print("\n[3/3] Writing updated raw_contracts.parquet...", flush=True)
    con.execute(f"COPY combined TO '{RAW_CONTRACTS_PATH}' (FORMAT PARQUET);")
    
    elapsed = time.time() - t0
    print(f"\n[OK] UK Contracts Finder ingestion completed in {elapsed:.1f}s.", flush=True)

if __name__ == "__main__":
    main()
