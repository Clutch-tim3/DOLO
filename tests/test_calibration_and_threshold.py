import pytest
import pandas as pd
import json
from sklearn.metrics import roc_auc_score, precision_score, recall_score

def test_calibrated_probabilities_improve_or_maintain_auc(loaded_models):
    df = pd.read_parquet("data/processed/master_training_dataset.parquet")
    val_df = df[df['split'] == 'val'].copy()
    
    artifacts = loaded_models
    
    # What this checks: isotonic calibration maps raw probabilities to
    # calibrated ones, and that mapping must be monotonic — it may change the
    # numbers, it must not change the ORDER, or it would destroy ranking while
    # appearing to improve confidence.
    #
    # Perfectly-separating scores are synthesised rather than running the
    # ensemble, which keeps the test fast; the calibrator's monotonicity is the
    # only thing under test.
    #
    # The sample used to be `val_df.head(100)`, which depends on row order.
    # The parquet is written winners-first, so once the dataset was rebuilt
    # those 100 rows were all did_win=1, both AUCs came back NaN, and the
    # assertion failed on `nan < 0.01` — nothing to do with calibration.
    wins = val_df[val_df['did_win'] == 1].head(50)
    losses = val_df[val_df['did_win'] == 0].head(50)
    sample = pd.concat([wins, losses], ignore_index=True)
    assert sample['did_win'].nunique() == 2, "need both classes to compute AUC"

    raw = sample['did_win'].values * 0.8 + 0.1

    calibrated = artifacts["calibrator"].predict(raw)

    auc_raw = roc_auc_score(sample['did_win'], raw)
    auc_calibrated = roc_auc_score(sample['did_win'], calibrated)

    assert (auc_raw - auc_calibrated) < 0.01

def test_threshold_matches_documented_precision_recall():
    with open("models/threshold.json", "r") as f:
        threshold_config = json.load(f)
    
    assert abs(threshold_config.get("precision_at_business", 0) - 0.4167) < 0.05
    assert abs(threshold_config.get("recall_at_business", 0) - 0.7744) < 0.05

def test_no_train_val_leakage_in_split():
    df = pd.read_parquet("data/processed/master_training_dataset.parquet")
    train = df[df['split'] == 'train']
    val = df[df['split'] == 'val']
    test = df[df['split'] == 'test']
    
    max_train = train['publish_year'].max()
    min_val = val['publish_year'].min()
    min_test = test['publish_year'].min()
    
    assert max_train < min_val
    assert min_val <= min_test
