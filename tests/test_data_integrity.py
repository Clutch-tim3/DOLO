import pytest
import subprocess
from pathlib import Path
import sqlite3
import pandas as pd

def test_no_known_test_constants_in_production_code():
    # Grep codebase for known leftover test values.
    # Exclude /tests/ dir and .venv or anything like that.
    
    project_root = Path(__file__).parent.parent
    # We use ripgrep or simple python search
    constants = ["us_36008769", "EASTERN CAROLINA VOCATIONAL", "DUMMY", "TODO", "FIXME"]
    
    found_issues = []
    
    for py_file in project_root.rglob("*.py"):
        if "tests" in py_file.parts or ".venv" in py_file.parts or "patch_" in py_file.name:
            continue
            
        text = py_file.read_text(errors='ignore')
        for c in constants:
            if c in text:
                found_issues.append(f"Found {c} in {py_file.name}")
                
    assert len(found_issues) == 0, "\\n".join(found_issues)

def test_no_hardcoded_fallback_probability():
    project_root = Path(__file__).parent.parent
    
    found_issues = []
    
    for py_file in project_root.rglob("*.py"):
        if "tests" in py_file.parts or ".venv" in py_file.parts or "patch_" in py_file.name:
            continue
            
        text = py_file.read_text(errors='ignore')
        if "0.175" in text or "0.293" in text or "0.58" in text:
            found_issues.append(f"Found hardcoded float literal in {py_file.name}")
            
    assert len(found_issues) == 0, "\\n".join(found_issues)

def test_sqlite_parquet_schema_agreement():
    # Only need to test master_training_dataset
    db_path = Path(__file__).parent.parent / "data/procurement.db"
    parquet_path = Path(__file__).parent.parent / "data/processed/master_training_dataset.parquet"
    
    if not db_path.exists() or not parquet_path.exists():
        pytest.skip("DB or parquet not found")
        
    conn = sqlite3.connect(db_path)
    df_sql = pd.read_sql("SELECT * FROM master_training_dataset LIMIT 1", conn)
    conn.close()
    
    df_pq = pd.read_parquet(parquet_path).head(1)
    
    assert set(df_sql.columns) == set(df_pq.columns)

def test_no_orphaned_debug_prints_in_production():
    project_root = Path(__file__).parent.parent
    
    found_issues = []
    
    for py_file in project_root.rglob("*.py"):
        if "tests" in py_file.parts or ".venv" in py_file.parts or "patch_" in py_file.name:
            continue
            
        if py_file.name in ["app.py", "predict.py"]:
            text = py_file.read_text(errors='ignore')
            if "print(features_df" in text or "print(parsed" in text or "print(tender_text)" in text:
                found_issues.append(f"Found orphaned debug print in {py_file.name}")
            
    assert len(found_issues) == 0, "\\n".join(found_issues)
