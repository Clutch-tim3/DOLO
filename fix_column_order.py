import pandas as pd

df_train = pd.read_parquet('data/splits/X_train.parquet')
cols = df_train.columns.tolist()

for split in ['X_val', 'X_test']:
    df = pd.read_parquet(f'data/splits/{split}.parquet')
    df = df[cols]
    df.to_parquet(f'data/splits/{split}.parquet')

print("Reordered X_val and X_test to match X_train.")
