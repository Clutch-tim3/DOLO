import sys
import pickle
from pathlib import Path
import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MASTER_DATASET_PATH = PROJECT_ROOT / "data" / "processed" / "master_training_dataset.parquet"
X_VAL_PATH = PROJECT_ROOT / "data" / "splits" / "X_val.parquet"
X_TEST_PATH = PROJECT_ROOT / "data" / "splits" / "X_test.parquet"
NEW_MEDIANS_PATH = PROJECT_ROOT / "data" / "processed" / "new_feature_medians.pkl"

def main():
    print("Loading master dataset...")
    df = pd.read_parquet(MASTER_DATASET_PATH)
    
    print("Adding FEATURE 1: experience_tier...")
    bins = [-1, 0, 5, 20, 50, np.inf]
    labels = [0, 1, 2, 3, 4]
    df['experience_tier'] = pd.cut(df['pit_total_wins'].fillna(0), bins=bins, labels=labels).astype(int)
    
    print("Adding FEATURE 2: win_momentum...")
    df['win_momentum'] = (df['pit_total_wins'] * df['pit_recency_score']).round(4)
    
    print("Adding FEATURE 3: buyer_openness_score...")
    # Calculate only from training data to avoid leakage
    train_df = df[df['split'] == 'train']
    wins = train_df[train_df['did_win'] == 1]
    
    # openness = count(did_win=1 AND pit_is_incumbent=0) / count(did_win=1)
    # per buyer
    grouped = wins.groupby('buyer_masterid')
    openness_series = grouped.apply(lambda x: (x['pit_is_incumbent'] == 0).sum() / len(x))
    openness_df = openness_series.reset_index(name='buyer_openness_score')
    
    global_mean_openness = openness_df['buyer_openness_score'].mean()
    
    df = df.merge(openness_df, on='buyer_masterid', how='left')
    df['buyer_openness_score'] = df['buyer_openness_score'].fillna(global_mean_openness).round(4)
    
    new_features = ['experience_tier', 'win_momentum', 'buyer_openness_score']
    
    print("\nFeature Statistics:")
    for feat in new_features:
        mean_val = df[feat].mean()
        std_val = df[feat].std()
        min_val = df[feat].min()
        max_val = df[feat].max()
        corr = df[feat].corr(df['did_win'])
        nulls = df[feat].isnull().sum()
        
        print(f"--- {feat} ---")
        print(f"Mean: {mean_val:.4f}, Std: {std_val:.4f}, Min: {min_val:.4f}, Max: {max_val:.4f}")
        print(f"Correlation with did_win: {corr:.4f}")
        print(f"Null count: {nulls}")
        if nulls > 0:
            print("ERROR: Null count should be 0")
            sys.exit(1)
            
    print("\nSaving updated master dataset...")
    df.to_parquet(MASTER_DATASET_PATH)
    
    print("Rebuilding val and test splits...")
    # Get original X_val and X_test to see what columns they have
    X_val_orig = pd.read_parquet(X_VAL_PATH)
    X_test_orig = pd.read_parquet(X_TEST_PATH)
    
    # We need to extract the new features for val and test, and attach them
    # But note: in 06_preprocess.py, X_val and X_test dropped some columns.
    # Master dataset has all columns. We can merge the new features onto X_val_orig and X_test_orig
    # using index or pair_id, BUT X_val_orig doesn't have pair_id, it only has encoded features!
    # Let's check if they have pair_id or if they just have the same index as the master df
    # X_val, X_test were split from master without resetting index. So indexes match!
    # Wait, master_dataset may have been sorted or filtered? Let's use indices.
    
    val_indices = df[df['split'] == 'val'].index
    test_indices = df[df['split'] == 'test'].index
    
    # Make sure X_val_orig length matches val_indices length
    if len(X_val_orig) == len(val_indices):
        X_val_new = X_val_orig.copy()
        for f in new_features:
            X_val_new[f] = df.loc[val_indices, f].values
        X_val_new.to_parquet(X_VAL_PATH)
        print(f"Saved {X_VAL_PATH} with {X_val_new.shape[1]} columns")
    else:
        print("ERROR: Length mismatch for val split!")
        sys.exit(1)
        
    if len(X_test_orig) == len(test_indices):
        X_test_new = X_test_orig.copy()
        for f in new_features:
            X_test_new[f] = df.loc[test_indices, f].values
        X_test_new.to_parquet(X_TEST_PATH)
        print(f"Saved {X_TEST_PATH} with {X_test_new.shape[1]} columns")
    else:
        print("ERROR: Length mismatch for test split!")
        sys.exit(1)
        
    print("Computing and saving new feature medians from train split...")
    train_new_feats = df.loc[df['split'] == 'train', new_features]
    new_medians = {
        'experience_tier': float(train_new_feats['experience_tier'].median()),
        'win_momentum': float(train_new_feats['win_momentum'].median()),
        'buyer_openness_score': float(train_new_feats['buyer_openness_score'].median())
    }
    
    # Wait, buyer_openness_score for completely unknown buyers in production
    # Should it be the global mean? Yes, we already set the global mean in the dataframe.
    # The median in the training set will be used as fallback in predict.py.
    # To match instructions: "Fill null (unknown buyers) with global mean openness score"
    # Wait, if `predict.py` uses medians for EVERYTHING missing, it will use the median of buyer_openness_score.
    # But wait, median of buyer_openness_score might be different from global mean openness score.
    # Let's save the global mean as the "median" for buyer_openness_score so `predict.py` uses it!
    new_medians['buyer_openness_score'] = float(global_mean_openness)
    
    with open(NEW_MEDIANS_PATH, 'wb') as f:
        pickle.dump(new_medians, f)
    
    print(f"Saved new feature medians to {NEW_MEDIANS_PATH}")
    print("New medians:", new_medians)
    
if __name__ == "__main__":
    main()
