#!/usr/bin/env python3
"""
02b_ingest_ocds.py — Ingest South African OCDS dataset into the unified GPPD schema.
"""

import sys
import time
from pathlib import Path
import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
RAW_CONTRACTS_PATH = PROCESSED_DIR / "raw_contracts.parquet"
SA_RAW_DIR = Path(r"C:\Users\Thabang\Desktop\Data set V2\data\raw\full")

OCDS_AWARDS_SUPPLIERS = SA_RAW_DIR / "awards_suppliers.csv"
OCDS_AWARDS = SA_RAW_DIR / "awards.csv"
OCDS_MAIN = SA_RAW_DIR / "main.csv"

def main():
    t0 = time.time()
    print("=" * 70, flush=True)
    print("  02b_ingest_ocds.py — South Africa OCDS Data Ingestion", flush=True)
    print("=" * 70, flush=True)

    for path in [OCDS_AWARDS_SUPPLIERS, OCDS_AWARDS, OCDS_MAIN]:
        if not path.exists():
            print(f"ERROR: Required OCDS file missing: {path}", flush=True)
            sys.exit(1)

    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit = '4GB';")
    con.execute("SET threads = 4;")

    print("\n[1/3] Reading SA OCDS CSVs and mapping to GPPD schema...", flush=True)

    sa_main = OCDS_MAIN.as_posix()
    sa_awards = OCDS_AWARDS.as_posix()
    sa_supp = OCDS_AWARDS_SUPPLIERS.as_posix()

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
            TRY_CAST(NULL AS TIMESTAMP) as tender_awarddecisiondate,
            m.tender_tenderPeriod_endDate as tender_biddeadline,
            1 as tender_recordedbidscount,
            1 as lot_bidscount,
            1 as tender_lotscount,
            1 as tender_awardcriteria_count,
            'MEAT' as tender_selectionmethod,
            LENGTH(COALESCE(m.tender_description, '')) as tender_description_length,
            LENGTH(COALESCE(m.tender_description, '')) as lot_description_length,
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
            m.tender_value_currency as currency,
            True as filter_ok,
            False as filter_losingbids,
            False as filter_cancelled,
            NULL as lot_updateddurationdays,
            0 as tender_corrections_count,
            'AWARDED' as lot_status,
            'ZA' as source_country
        FROM read_csv_auto('{sa_main}', ignore_errors=true) m
        INNER JOIN read_csv_auto('{sa_awards}', ignore_errors=true) a ON m.ocid = a.main_ocid
        INNER JOIN read_csv_auto('{sa_supp}', ignore_errors=true) s ON a.main_ocid = s.main_ocid AND a.id = s.awards_id
    """)

    sa_cnt = con.execute("SELECT COUNT(*) FROM ocds_mapped").fetchone()[0]
    print(f"  -> Extracted {sa_cnt:,} SA award records", flush=True)

    print("\n[2/3] Writing raw_contracts.parquet...", flush=True)
    con.execute(f"COPY ocds_mapped TO '{RAW_CONTRACTS_PATH.as_posix()}' (FORMAT PARQUET);")
    
    elapsed = time.time() - t0
    print(f"\n[OK] SA OCDS ingestion completed in {elapsed:.1f}s.", flush=True)

if __name__ == "__main__":
    main()
