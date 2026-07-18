import pandas as pd
import numpy as np

print("Fixing X_train...")
df = pd.read_parquet('data/splits/X_train.parquet')

# Adding missing 3 features
bins = [-1, 0, 5, 20, 50, np.inf]
labels = [0, 1, 2, 3, 4]
if 'pit_total_wins' in df.columns:
    df['experience_tier'] = pd.cut(df['pit_total_wins'].fillna(0), bins=bins, labels=labels).astype(int)
    df['win_momentum'] = (df['pit_total_wins'] * df['pit_recency_score']).round(4)
else:
    df['experience_tier'] = 0
    df['win_momentum'] = 0.0

# buyer_openness_score is trickier because we need the global mean, but the median was saved in medians.pkl
import pickle
with open('data/processed/medians.pkl', 'rb') as f:
    m = pickle.load(f)
df['buyer_openness_score'] = m.get('buyer_openness_score', 0.5)

print("Columns before save:", len(df.columns))
df.to_parquet('data/splits/X_train.parquet')
print("Fixed!")
