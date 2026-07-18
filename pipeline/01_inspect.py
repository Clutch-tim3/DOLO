#!/usr/bin/env python3
"""
01_inspect.py — Inspect the GPPD file(s) without loading into RAM.

Uses DuckDB to query CSV.GZ files directly. Stays under 500MB RAM.
Processes each file independently for efficiency (avoids re-decompressing).
"""

import sys
import duckdb
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT.parent / "data" / "raw" / "GTI Global Public Procurement Dataset (GPPD) 22"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

DATA_FILES = sorted(RAW_DIR.glob("*.csv.gz"))

if not DATA_FILES:
    print(f"ERROR: No .csv.gz files found in {RAW_DIR}")
    sys.exit(1)

PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
REPORT_PATH = PROCESSED_DIR / "schema_report.txt"

# ── Helpers ────────────────────────────────────────────────────────────────
report_lines = []


def log(msg=""):
    print(msg, flush=True)
    report_lines.append(msg)


def fpath_sql(f):
    """Return an escaped file path for SQL."""
    return str(f).replace("'", "''")


def read_table(con, f):
    """Return a DuckDB table reference for a CSV.GZ file."""
    fp = fpath_sql(f)
    return f"read_csv_auto('{fp}', sample_size=10000, ignore_errors=true, header=true)"


# ══════════════════════════════════════════════════════════════════════════
def run_inspection():
    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit = '400MB';")
    con.execute("SET threads = 4;")

    log("=" * 80)
    log("  GPPD DATA INSPECTION REPORT")
    log("=" * 80)
    log()

    # ── Files found ────────────────────────────────────────────────────────
    log(f"Files found in: {RAW_DIR}")
    for f in DATA_FILES:
        size_gb = f.stat().st_size / (1024 ** 3)
        log(f"  • {f.name}  ({size_gb:.2f} GB)")
    log()

    # We'll inspect the FIRST file for schema/samples, then get row counts
    # for all files. This avoids decompressing all files multiple times.
    primary = DATA_FILES[0]
    tbl = read_table(con, primary)

    # ── 1. Schema from first file ──────────────────────────────────────────
    log("─── 1. COLUMNS AND DATA TYPES ──────────────────────────────────")
    log(f"  (Schema from: {primary.name})")
    schema = con.execute(f"DESCRIBE SELECT * FROM {tbl}").fetchall()
    col_names = []
    log(f"  {'#':<4} {'Column Name':<55} {'Type':<20}")
    log(f"  {'─'*4} {'─'*55} {'─'*20}")
    for i, row in enumerate(schema):
        col_name, col_type = row[0], row[1]
        col_names.append(col_name)
        log(f"  {i+1:<4} {col_name:<55} {col_type:<20}")
    log(f"\n  Total columns: {len(col_names)}")
    log()

    # Check second file schema if exists
    if len(DATA_FILES) > 1:
        log("  Checking schema consistency across files...")
        tbl2 = read_table(con, DATA_FILES[1])
        schema2 = con.execute(f"DESCRIBE SELECT * FROM {tbl2}").fetchall()
        cols2 = [r[0] for r in schema2]
        if col_names == cols2:
            log("  ✓ All files have identical column schemas.")
        else:
            only_in_1 = set(col_names) - set(cols2)
            only_in_2 = set(cols2) - set(col_names)
            if only_in_1:
                log(f"  ⚠ Columns only in {DATA_FILES[0].name}: {only_in_1}")
            if only_in_2:
                log(f"  ⚠ Columns only in {DATA_FILES[1].name}: {only_in_2}")
        log()

    # ── 2. Row counts per file ─────────────────────────────────────────────
    log("─── 2. ROW COUNTS ──────────────────────────────────────────────")
    total_rows = 0
    for f in DATA_FILES:
        t = read_table(con, f)
        count = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        total_rows += count
        log(f"  {f.name}: {count:,} rows")
    log(f"  TOTAL: {total_rows:,} rows")
    log()

    # ── 3. Sample rows (from first file only) ──────────────────────────────
    log("─── 3. SAMPLE ROWS (3 rows from first file) ───────────────────")
    samples = con.execute(f"SELECT * FROM {tbl} LIMIT 3").fetchdf()
    for idx, row in samples.iterrows():
        log(f"\n  --- Row {idx + 1} ---")
        for col in samples.columns:
            val = str(row[col])
            if len(val) > 120:
                val = val[:120] + "..."
            log(f"    {col:<55} {val}")
    log()

    # ── 4. Null rates (from first file — representative) ───────────────────
    log("─── 4. NULL RATES ──────────────────────────────────────────────")
    log(f"  (Computed from: {primary.name})")
    null_parts = []
    for col in col_names:
        safe = f'"{col}"'
        null_parts.append(
            f"ROUND(100.0 * SUM(CASE WHEN {safe} IS NULL THEN 1 ELSE 0 END) "
            f"/ COUNT(*), 2) AS \"{col}\""
        )
    null_q = f"SELECT {', '.join(null_parts)} FROM {tbl}"
    null_rates = con.execute(null_q).fetchdf()

    log(f"  {'Column':<55} {'Null %':>8}")
    log(f"  {'─'*55} {'─'*8}")
    for col in col_names:
        rate = null_rates[col].values[0]
        marker = " ⚠ HIGH" if rate > 50 else ""
        log(f"  {col:<55} {rate:>7.2f}%{marker}")
    log()

    # ── 5. Key categorical value counts ────────────────────────────────────
    log("─── 5. KEY CATEGORICAL VALUE COUNTS ────────────────────────────")
    log(f"  (From: {primary.name})")

    keyword_groups = {
        "country": ["country", "nation", "iso"],
        "procedure/method": ["procedure", "method", "procurement"],
        "status": ["status", "state"],
        "winner/supplier": ["winner", "supplier", "vendor", "award", "contractor"],
        "buyer": ["buyer", "purchaser", "authority"],
        "tender type": ["tender_type", "contract_type", "type"],
    }

    for group_name, keywords in keyword_groups.items():
        matching_cols = [
            c for c in col_names
            if any(kw in c.lower() for kw in keywords)
        ]
        if matching_cols:
            log(f"\n  [{group_name.upper()}] Matching columns: {matching_cols}")
            for col in matching_cols[:3]:
                safe_col = f'"{col}"'
                try:
                    vc = con.execute(
                        f"SELECT {safe_col} AS val, COUNT(*) AS cnt "
                        f"FROM {tbl} "
                        f"WHERE {safe_col} IS NOT NULL "
                        f"GROUP BY {safe_col} "
                        f"ORDER BY cnt DESC LIMIT 15"
                    ).fetchdf()
                    log(f"\n    {col} — top values:")
                    for _, r in vc.iterrows():
                        val_str = str(r['val'])
                        if len(val_str) > 60:
                            val_str = val_str[:60] + "..."
                        log(f"      {val_str:<62} {r['cnt']:>12,}")
                except Exception as e:
                    log(f"    {col}: ERROR — {e}")
    log()

    # ── 6. Date range ──────────────────────────────────────────────────────
    log("─── 6. DATE RANGE ──────────────────────────────────────────────")
    date_cols = [c for c in col_names if any(kw in c.lower() for kw in
                 ["date", "year", "period", "published", "deadline"])]
    if date_cols:
        log(f"  Date-like columns: {date_cols}")
        for col in date_cols[:6]:
            safe_col = f'"{col}"'
            try:
                result = con.execute(
                    f"SELECT MIN({safe_col}) AS mn, MAX({safe_col}) AS mx, "
                    f"COUNT(DISTINCT {safe_col}) AS nd "
                    f"FROM {tbl} WHERE {safe_col} IS NOT NULL"
                ).fetchdf()
                log(f"    {col}: {result['mn'].values[0]} → {result['mx'].values[0]} "
                    f"({result['nd'].values[0]:,} distinct)")
            except Exception as e:
                log(f"    {col}: ERROR — {e}")
    else:
        log("  No date columns found by keyword matching.")
        log(f"  All columns: {col_names}")
    log()

    # ── 7. Contract value distribution ─────────────────────────────────────
    log("─── 7. CONTRACT VALUE DISTRIBUTION ─────────────────────────────")
    value_cols = [c for c in col_names if any(kw in c.lower() for kw in
                  ["value", "amount", "price", "cost"])]
    if value_cols:
        log(f"  Value-like columns: {value_cols}")
        for col in value_cols[:3]:
            safe_col = f'"{col}"'
            try:
                stats = con.execute(
                    f"SELECT "
                    f"  COUNT(TRY_CAST({safe_col} AS DOUBLE)) AS n, "
                    f"  MIN(TRY_CAST({safe_col} AS DOUBLE)) AS min_val, "
                    f"  APPROX_QUANTILE(TRY_CAST({safe_col} AS DOUBLE), 0.25) AS p25, "
                    f"  APPROX_QUANTILE(TRY_CAST({safe_col} AS DOUBLE), 0.50) AS median, "
                    f"  APPROX_QUANTILE(TRY_CAST({safe_col} AS DOUBLE), 0.75) AS p75, "
                    f"  APPROX_QUANTILE(TRY_CAST({safe_col} AS DOUBLE), 0.95) AS p95, "
                    f"  MAX(TRY_CAST({safe_col} AS DOUBLE)) AS max_val, "
                    f"  AVG(TRY_CAST({safe_col} AS DOUBLE)) AS mean_val "
                    f"FROM {tbl} "
                    f"WHERE TRY_CAST({safe_col} AS DOUBLE) IS NOT NULL"
                ).fetchdf()
                log(f"\n    {col}:")
                log(f"      Count:  {stats['n'].values[0]:>15,}")
                log(f"      Min:    {stats['min_val'].values[0]:>15,.2f}")
                log(f"      P25:    {stats['p25'].values[0]:>15,.2f}")
                log(f"      Median: {stats['median'].values[0]:>15,.2f}")
                log(f"      Mean:   {stats['mean_val'].values[0]:>15,.2f}")
                log(f"      P75:    {stats['p75'].values[0]:>15,.2f}")
                log(f"      P95:    {stats['p95'].values[0]:>15,.2f}")
                log(f"      Max:    {stats['max_val'].values[0]:>15,.2f}")
            except Exception as e:
                log(f"    {col}: ERROR — {e}")
    else:
        log("  No value columns found by keyword matching.")
    log()

    # ── 8. Bidder / Tenderer columns ───────────────────────────────────────
    log("─── 8. BIDDER / TENDERER / LOT COLUMNS ─────────────────────────")
    bid_cols = [c for c in col_names if any(kw in c.lower() for kw in
                ["bid", "tender", "offer", "participant", "competitor", "lot"])]
    if bid_cols:
        log(f"  Bidding-related columns: {bid_cols}")
        for col in bid_cols[:5]:
            safe_col = f'"{col}"'
            try:
                vc = con.execute(
                    f"SELECT {safe_col} AS val, COUNT(*) AS cnt "
                    f"FROM {tbl} WHERE {safe_col} IS NOT NULL "
                    f"GROUP BY {safe_col} ORDER BY cnt DESC LIMIT 10"
                ).fetchdf()
                log(f"\n    {col} — sample values:")
                for _, r in vc.iterrows():
                    val_str = str(r['val'])
                    if len(val_str) > 60:
                        val_str = val_str[:60] + "..."
                    log(f"      {val_str:<62} {r['cnt']:>12,}")
            except Exception as e:
                log(f"    {col}: ERROR — {e}")
    else:
        log("  No bidder/tenderer columns found.")
    log()

    # ── 9. Number of bids / tenders received ───────────────────────────────
    log("─── 9. NUMBER OF BIDS / TENDERS RECEIVED ──────────────────────")
    nbid_cols = [c for c in col_names if any(kw in c.lower() for kw in
                 ["num_bid", "number_of_bid", "n_bid", "bids_received",
                  "number_of_tender", "tenders_received", "numbertend",
                  "numberofbid", "num_tend", "nb_bid", "nb_tend"])]
    if nbid_cols:
        for col in nbid_cols:
            safe_col = f'"{col}"'
            try:
                stats = con.execute(
                    f"SELECT MIN(TRY_CAST({safe_col} AS INT)) AS mn, "
                    f"MAX(TRY_CAST({safe_col} AS INT)) AS mx, "
                    f"AVG(TRY_CAST({safe_col} AS DOUBLE)) AS avg_val, "
                    f"COUNT(*) AS n "
                    f"FROM {tbl} WHERE {safe_col} IS NOT NULL"
                ).fetchdf()
                log(f"  {col}: min={stats['mn'].values[0]}, max={stats['mx'].values[0]}, "
                    f"avg={stats['avg_val'].values[0]:.1f}, non-null={stats['n'].values[0]:,}")
            except Exception as e:
                log(f"  {col}: ERROR — {e}")
    else:
        log("  No explicit num_bids column found by name pattern.")
        log("  Checking columns with 'num', 'count', 'nb' in name:")
        possible = [c for c in col_names if any(kw in c.lower()
                    for kw in ["num", "count", "nb_", "number"])]
        for col in possible:
            log(f"    → {col}")
        if not possible:
            log("    (none found)")
    log()

    # ── Summary ────────────────────────────────────────────────────────────
    log("=" * 80)
    log("  INSPECTION COMPLETE")
    log(f"  Total rows across all files: {total_rows:,}")
    log(f"  Total columns: {len(col_names)}")
    log(f"  Files inspected: {len(DATA_FILES)}")
    log(f"  Report saved to: {REPORT_PATH}")
    log("=" * 80)

    # ── Save report ────────────────────────────────────────────────────────
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(report_lines))

    con.close()


if __name__ == "__main__":
    run_inspection()
