import pandas as pd
import json
import pickle
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def main():
    print("Updating X_train, X_val, X_test in memory...")
    splits = ["X_train", "X_val", "X_test"]
    
    for split in splits:
        split_path = PROJECT_ROOT / "data" / "splits" / f"{split}.parquet"
        if not split_path.exists():
            continue
            
        print(f"Loading {split}...")
        df = pd.read_parquet(split_path)
        
        # 1. Eligibility features
        df['had_functionality_gate'] = 0
        df['functionality_threshold_pct'] = 0.0
        df['requires_local_presence'] = 0
        df['supplier_matches_required_locality'] = 0
        df['requires_past_contract_proof'] = 0
        df['mandatory_cert_count'] = 0
        
        # 2. Missing features
        if 'pit_buyer_win_count' in df.columns and 'pit_buyer_entry_count' in df.columns:
            df['buyer_loyalty_score'] = (df['pit_buyer_win_count'] / df['pit_buyer_entry_count'].replace(0, pd.NA)).fillna(0.0)
        else:
            df['buyer_loyalty_score'] = 0.0
            
        df['category_hhi'] = 0.5
        
        if 'pit_win_rate_overall' in df.columns and 'pit_recency_score' in df.columns:
            df['supplier_momentum'] = (df['pit_win_rate_overall'] * df['pit_recency_score']).fillna(0.0)
        else:
            df['supplier_momentum'] = 0.0
            
        print(f"Saving {split}...")
        df.to_parquet(split_path, index=False)

    # 3. Update metadata
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
                    'requires_past_contract_proof', 'mandatory_cert_count',
                    'buyer_loyalty_score', 'category_hhi', 'supplier_momentum'
                ]
                if isinstance(feat_list, dict):
                    for nf in new_feats:
                        feat_list[nf] = "float64"
                elif isinstance(feat_list, list):
                    for nf in new_feats:
                        if nf not in feat_list:
                            feat_list.append(nf)
                            
        metadata["feature_count"] = metadata.get("feature_count", 0) + 9
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=4)
            
    # 4. Update medians
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
            'mandatory_cert_count': 0,
            'buyer_loyalty_score': 0.0,
            'category_hhi': 0.5,
            'supplier_momentum': 0.0
        })
        with open(old_medians_path, "wb") as f:
            pickle.dump(old_m, f)
            
    print("Done successfully.")

if __name__ == "__main__":
    main()
