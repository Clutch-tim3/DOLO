#!/usr/bin/env python3
"""
02b_ingest_ocds.py — Ingest South African OCDS dataset into the unified GPPD schema.

Reads the OCDS files (main.csv, awards.csv, awards_suppliers.csv) and maps them to the
GPPD schema, then appends to raw_contracts.parquet.
"""

import sys
import time
from pathlib import Path
import duckdb
import gc

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
RAW_CONTRACTS_PATH = PROCESSED_DIR / "raw_contracts.parquet"

OCDS_AWARDS_SUPPLIERS = PROJECT_ROOT.parent / "data" / "raw" / "full" / "awards_suppliers.csv"
OCDS_AWARDS = PROJECT_ROOT.parent / "data" / "raw" / "full" / "awards.csv"
OCDS_MAIN = PROJECT_ROOT.parent / "data" / "raw" / "full" / "main.csv"

def main():
    t0 = time.time()
    print("=" * 70, flush=True)
    print("  02b_ingest_ocds.py — South Africa OCDS Data Ingestion", flush=True)
    print("=" * 70, flush=True)

    if not RAW_CONTRACTS_PATH.exists():
        print(f"ERROR: Base {RAW_CONTRACTS_PATH} does not exist.", flush=True)
        sys.exit(1)

    for path in [OCDS_AWARDS_SUPPLIERS, OCDS_AWARDS, OCDS_MAIN]:
        if not path.exists():
            print(f"ERROR: Required OCDS file missing: {path}", flush=True)
            sys.exit(1)

    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit = '4GB';")
    con.execute("SET threads = 4;")

    print("\n[1/3] Reading OCDS CSVs and mapping to GPPD schema...", flush=True)
    
    # We define a CTE that transforms the OCDS data to match GPPD columns.
    # Note: GPPD columns might have specific types, so we try to cast appropriately.
    # The required GPPD columns from 02_ingest are:
    # persistent_id, tender_id, lot_id, bid_id, buyer_masterid, buyer_name, buyer_country, buyer_buyertype,
    # bidder_masterid, bidder_name, bidder_country, bid_iswinning, tender_proceduretype, tender_supplytype, tender_cpvs,
    # bid_priceUsd, tender_digiwhist_price, bid_digiwhist_price, tender_estimatedpriceUsd, tender_finalpriceUsd,
    # tender_year, tender_publications_firstcallfortenderdate, tender_awarddecisiondate, tender_biddeadline,
    # tender_recordedbidscount, lot_bidscount, tender_lotscount, tender_awardcriteria_count, tender_selectionmethod,
    # tender_description_length, lot_description_length, tender_personalrequirements_length, tender_technicalrequirements_length,
    # tender_economicrequirements_length, submission_period, corr_singleb, corr_proc, corr_subm, corr_buyer_concentration, cri,
    # currency, filter_ok, filter_losingbids, filter_cancelled, lot_updateddurationdays, tender_corrections_count,
    # lot_status, source_country

    con.execute(f"""
        CREATE TABLE ocds_mapped AS 
        SELECT 
            m.ocid as persistent_id,
            m.tender_id as tender_id,
            CAST(a.id AS VARCHAR) as lot_id,
            CAST(s.id AS VARCHAR) as bid_id,
            CAST(m.buyer_id AS VARCHAR) as buyer_masterid,
            m.buyer_name as buyer_name,
            'ZA' as buyer_country,
            'UNKNOWN' as buyer_buyertype,
            s.name as bidder_masterid,
            s.name as bidder_name,
            'ZA' as bidder_country,
            True as bid_iswinning,
            m.tender_procurementMethod as tender_proceduretype,
            m.tender_mainProcurementCategory as tender_supplytype,
            m.tender_category as tender_cpvs,
            TRY_CAST(a.value_amount AS DOUBLE) as bid_priceUsd,
            TRY_CAST(a.value_amount AS DOUBLE) as tender_digiwhist_price,
            TRY_CAST(a.value_amount AS DOUBLE) as bid_digiwhist_price,
            TRY_CAST(m.tender_value_amount AS DOUBLE) as tender_estimatedpriceUsd,
            TRY_CAST(m.tender_value_amount AS DOUBLE) as tender_finalpriceUsd,
            EXTRACT(YEAR FROM m.date) as tender_year,
            m.date as tender_publications_firstcallfortenderdate,
            m.date as tender_awarddecisiondate,
            m.tender_tenderPeriod_endDate as tender_biddeadline,
            1 as tender_recordedbidscount,
            1 as lot_bidscount,
            1 as tender_lotscount,
            1 as tender_awardcriteria_count,
            m.tender_procurementMethodDetails as tender_selectionmethod,
            LENGTH(m.tender_description) as tender_description_length,
            LENGTH(a.description) as lot_description_length,
            NULL::INTEGER as tender_personalrequirements_length,
            NULL::INTEGER as tender_technicalrequirements_length,
            NULL::INTEGER as tender_economicrequirements_length,
            NULL::INTEGER as submission_period,
            NULL::DOUBLE as corr_singleb,
            NULL::DOUBLE as corr_proc,
            NULL::DOUBLE as corr_subm,
            NULL::DOUBLE as corr_buyer_concentration,
            NULL::DOUBLE as cri,
            a.value_currency as currency,
            1 as filter_ok,
            0 as filter_losingbids,
            0 as filter_cancelled,
            NULL::INTEGER as lot_updateddurationdays,
            0 as tender_corrections_count,
            m.tender_status as lot_status,
            'ZA' as source_country
        FROM read_csv_auto('{OCDS_AWARDS_SUPPLIERS}', ignore_errors=true) s
        JOIN read_csv_auto('{OCDS_AWARDS}', ignore_errors=true) a ON s.awards_id = a.id
        JOIN read_csv_auto('{OCDS_MAIN}', ignore_errors=true) m ON a.main_id = m.id
    """)

    count_new = con.execute("SELECT COUNT(*) FROM ocds_mapped").fetchone()[0]
    print(f"  → Mapped {count_new:,} new rows from South Africa OCDS.", flush=True)

    print("\n[2/3] Unioning with existing GPPD data...", flush=True)
    temp_path = PROCESSED_DIR / "raw_contracts_temp.parquet"

    # We read the schema of raw_contracts to ensure alignment
    gppd_cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{RAW_CONTRACTS_PATH}') LIMIT 1").fetchall()]
    
    # Ensure ocds_mapped matches gppd_cols in order
    select_cols = []
    for col in gppd_cols:
        select_cols.append(f'"{col}"')
    
    select_clause = ", ".join(select_cols)
    
    con.execute(f"""
        COPY (
            SELECT {select_clause} FROM read_parquet('{RAW_CONTRACTS_PATH}')
            UNION ALL
            SELECT {select_clause} FROM ocds_mapped
        ) TO '{temp_path}' (FORMAT 'parquet', COMPRESSION 'zstd')
    """)

    print(f"\n[3/3] Replacing old file with updated dataset...", flush=True)
    RAW_CONTRACTS_PATH.unlink()
    temp_path.rename(RAW_CONTRACTS_PATH)
    
    total_count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{RAW_CONTRACTS_PATH}')").fetchone()[0]
    print(f"  → Total rows in raw_contracts.parquet: {total_count:,}", flush=True)
    
    print(f"\n{'='*70}", flush=True)
    print(f"  INGESTION COMPLETE in {time.time() - t0:.1f}s", flush=True)
    print(f"{'='*70}", flush=True)

if __name__ == "__main__":
    main()
