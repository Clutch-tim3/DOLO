import duckdb
import json
import pickle
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def main():
    print("Adding missing features using DuckDB...")
    con = duckdb.connect()
    
    splits = ["master_training_dataset", "X_train", "X_val", "X_test"]
    for split in splits:
        if split == "master_training_dataset":
            split_path = PROJECT_ROOT / "data" / "processed" / f"{split}.parquet"
        else:
            split_path = PROJECT_ROOT / "data" / "splits" / f"{split}.parquet"
            
        if split_path.exists():
            print(f"Processing {split}...")
            # buyer_loyalty_score: approx by (pit_buyer_win_count / pit_total_wins) or 0
            # category_hhi: 0.5 (mock, missing category data)
            # supplier_momentum: pit_win_rate_overall * pit_recency_score
            con.execute(f"""
                COPY (
                    SELECT *, 
                           COALESCE(CAST(pit_buyer_win_count AS DOUBLE) / NULLIF(pit_buyer_entry_count, 0), 0.0) AS buyer_loyalty_score,
                           0.5::DOUBLE AS category_hhi,
                           COALESCE(pit_win_rate_overall * pit_recency_score, 0.0) AS supplier_momentum
                    FROM read_parquet('{split_path}')
                ) TO '{split_path}.tmp' (FORMAT PARQUET)
            """)
            Path(f"{split_path}.tmp").rename(split_path)

    # Update metadata
    metadata_path = PROJECT_ROOT / "data" / "processed" / "dataset_metadata.json"
    if metadata_path.exists():
        with open(metadata_path, "r") as f:
            metadata = json.load(f)
            
        keys_to_check = ["features", "feature_names", "feature_columns", "columns"]
        for key in keys_to_check:
            if key in metadata:
                feat_list = metadata[key]
                new_feats = ['buyer_loyalty_score', 'category_hhi', 'supplier_momentum']
                if isinstance(feat_list, dict):
                    for nf in new_feats:
                        feat_list[nf] = "float64"
                elif isinstance(feat_list, list):
                    for nf in new_feats:
                        if nf not in feat_list:
                            feat_list.append(nf)
                            
        metadata["feature_count"] = metadata.get("feature_count", 0) + 3
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=4)
            
    # Update medians
    old_medians_path = PROJECT_ROOT / "data" / "processed" / "medians.pkl"
    if old_medians_path.exists():
        with open(old_medians_path, "rb") as f:
            old_m = pickle.load(f)
        old_m.update({
            'buyer_loyalty_score': 0.0,
            'category_hhi': 0.5,
            'supplier_momentum': 0.0
        })
        with open(old_medians_path, "wb") as f:
            pickle.dump(old_m, f)
            
    print("Done adding missing features.")

if __name__ == "__main__":
    main()
