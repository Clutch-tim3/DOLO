import json
import sqlite3
import pandas as pd
from pathlib import Path

metrics_path = Path("models/metrics_conquest.json")
db_path = Path("data/procurement.db")

# 1. Update models/metrics_conquest.json
if metrics_path.exists():
    with open(metrics_path, "r") as f:
        metrics = json.load(f)
    
    metrics["status"] = "not yet evaluated — no production-scale Conquest run has occurred"
    metrics["production_run"] = False
    
    # Clearly invalidate test/val stats with status messages
    metrics["test"]["roc_auc"] = "not yet evaluated — no production-scale Conquest run has occurred"
    metrics["test"]["log_loss"] = "not yet evaluated — no production-scale Conquest run has occurred"
    metrics["test"]["precision_at_20"] = None
    
    metrics["val"]["roc_auc"] = "not yet evaluated — no production-scale Conquest run has occurred"
    metrics["val"]["log_loss"] = "not yet evaluated — no production-scale Conquest run has occurred"
    metrics["val"]["precision_at_20"] = None
    
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print("Retracted metrics in models/metrics_conquest.json successfully.")

# 2. Update model_metrics_conquest in procurement.db
if db_path.exists():
    conn = sqlite3.connect(str(db_path))
    # Check if table exists
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='model_metrics_conquest'")
    if c.fetchone():
        c.execute("DROP TABLE model_metrics_conquest")
        conn.commit()
    
    # Create clean empty or status table
    df_retracted = pd.DataFrame([{
        "train_auc": None,
        "val_auc": None,
        "test_auc": None,
        "model_version": "conquest",
        "status": "not yet evaluated — no production-scale Conquest run has occurred"
    }])
    df_retracted.to_sql("model_metrics_conquest", conn, if_exists="replace", index=False)
    conn.close()
    print("Retracted metrics in SQLite table model_metrics_conquest successfully.")
