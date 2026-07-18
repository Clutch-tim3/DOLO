import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))
import pandas as pd
from predict.predict import load_all_artifacts, get_feature_list, predict, BOX_WIDTH

def run_scenarios():
    artifacts = load_all_artifacts()
    feature_list = get_feature_list(artifacts["metadata"])
    
    # Base feature dictionary using medians
    base_features = {feat: artifacts["medians"].get(feat, 0) for feat in feature_list}
    
    scenarios = [
        {
            "name": "Scenario 1: New bidder, low competition category",
            "overrides": {
                "pit_total_wins": 0,
                "pit_is_incumbent": 0,
                "actual_num_competitors": 2, # Low competition
                "category_hhi": 0.8 # Highly concentrated (often means less competition)
            }
        },
        {
            "name": "Scenario 2: Experienced bidder, 5 past wins, not incumbent",
            "overrides": {
                "pit_total_wins": 5,
                "pit_experience_score": 1.79, # log(1+5)
                "pit_is_incumbent": 0,
                "pit_recency_score": 0.5, # 1 / (1 + 1)
                "actual_num_competitors": 5 # Average competition
            }
        },
        {
            "name": "Scenario 3: Incumbent, won with this buyer 3 times before",
            "overrides": {
                "pit_total_wins": 12,
                "pit_experience_score": 2.56,
                "pit_is_incumbent": 1,
                "pit_buyer_win_count": 3,
                "pit_win_rate_buyer": 0.6,
                "pit_recency_score": 1.0, # won recently
                "actual_num_competitors": 5
            }
        }
    ]
    
    for s in scenarios:
        # Create a copy of base features and apply overrides
        features = base_features.copy()
        for k, v in s["overrides"].items():
            if k in features:
                features[k] = v
                
        df = pd.DataFrame([features])
        
        # We don't need to encode/impute because we're providing raw numeric values directly 
        # (categorical features are just using the median encoded values for now, which is fine for this demo)
        
        # Ensure exact column order
        if artifacts["xgb_model"].feature_names is not None:
            df = df[artifacts["xgb_model"].feature_names]
            
        result = predict(artifacts, df)
        
        prob_pct = f"{result['probability'] * 100:.1f}%"
        print(f"\n╔{'═'*BOX_WIDTH}╗")
        print(f"║{s['name'][:BOX_WIDTH-2]:^{BOX_WIDTH}}║")
        print(f"╠{'═'*BOX_WIDTH}╣")
        print(f"║ {'Win Probability:':<22}{prob_pct:<{BOX_WIDTH-24}} ║")
        print(f"║ {'Recommendation:':<22}{result['recommendation']:<{BOX_WIDTH-24}} ║")
        print(f"╚{'═'*BOX_WIDTH}╝\n")

if __name__ == "__main__":
    run_scenarios()
