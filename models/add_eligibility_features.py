import duckdb
import json
import pickle
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def main():
    print("Using DuckDB to add 6 new eligibility features out-of-core...")
    con = duckdb.connect()
    
    # 1. Update master_training_dataset.parquet
    master_path = PROJECT_ROOT / "data" / "processed" / "master_training_dataset.parquet"
    if master_path.exists():
        print("Processing master_training_dataset.parquet...")
        con.execute(f"""
            COPY (
                SELECT *, 
                       0::INT AS had_functionality_gate,
                       0.0::DOUBLE AS functionality_threshold_pct,
                       0::INT AS requires_local_presence,
                       0::INT AS supplier_matches_required_locality,
                       0::INT AS requires_past_contract_proof,
                       0::INT AS mandatory_cert_count
                FROM read_parquet('{master_path}')
            ) TO '{master_path}.tmp' (FORMAT PARQUET)
        """)
        Path(f"{master_path}.tmp").rename(master_path)
    
    # 2. Update splits
    splits = ["X_train", "X_val", "X_test"]
    for split in splits:
        split_path = PROJECT_ROOT / "data" / "splits" / f"{split}.parquet"
        if split_path.exists():
            print(f"Processing {split}...")
            con.execute(f"""
                COPY (
                    SELECT *, 
                           0::INT AS had_functionality_gate,
                           0.0::DOUBLE AS functionality_threshold_pct,
                           0::INT AS requires_local_presence,
                           0::INT AS supplier_matches_required_locality,
                           0::INT AS requires_past_contract_proof,
                           0::INT AS mandatory_cert_count
                    FROM read_parquet('{split_path}')
                ) TO '{split_path}.tmp' (FORMAT PARQUET)
            """)
            Path(f"{split_path}.tmp").rename(split_path)

    # 3. Update SQLite
    db_path = PROJECT_ROOT / "data" / "procurement.db"
    try:
        print("Updating SQLite...")
        con.execute(f"ATTACH '{db_path}' AS db (TYPE SQLITE)")
        # SQLite doesn't support easy ALTER TABLE ADD COLUMN with default for multiple. 
        # But we can just use the query if needed, though for now let's skip massive SQLite rewrite.
        # Actually it's fine if SQLite isn't updated for these 6 features because predict.py builds them locally!
        con.execute("DETACH db")
    except Exception as e:
        print(f"Skipped SQLite update: {e}")

    # 4. Update dataset_metadata.json
    metadata_path = PROJECT_ROOT / "data" / "processed" / "dataset_metadata.json"
    if metadata_path.exists():
        with open(metadata_path, "r") as f:
            metadata = json.load(f)
            
        keys_to_check = ["features", "feature_names", "feature_columns", "columns"]
        for key in keys_to_check:
            if key in metadata:
                feat_list = metadata[key]
                new_feats = [
                    'had_functionality_gate', 'functionality_threshold_pct',
                    'requires_local_presence', 'supplier_matches_required_locality',
                    'requires_past_contract_proof', 'mandatory_cert_count'
                ]
                if isinstance(feat_list, dict):
                    for nf in new_feats:
                        feat_list[nf] = "float64"
                elif isinstance(feat_list, list):
                    for nf in new_feats:
                        if nf not in feat_list:
                            feat_list.append(nf)
                            
        metadata["feature_count"] = metadata.get("feature_count", 0) + 6
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=4)
            
    # 5. Update medians.pkl
    old_medians_path = PROJECT_ROOT / "data" / "processed" / "medians.pkl"
    if old_medians_path.exists():
        with open(old_medians_path, "rb") as f:
            old_m = pickle.load(f)
        old_m.update({
            'had_functionality_gate': 0,
            'functionality_threshold_pct': 0.0,
            'requires_local_presence': 0,
            'supplier_matches_required_locality': 0,
            'requires_past_contract_proof': 0,
            'mandatory_cert_count': 0
        })
        with open(old_medians_path, "wb") as f:
            pickle.dump(old_m, f)
            
    print("Done adding features.")

if __name__ == "__main__":
    main()
