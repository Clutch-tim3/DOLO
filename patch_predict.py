import sys
import re

with open('predict/predict.py', 'r') as f:
    content = f.read()

# 1. Remove mock scenarios in extract_features_from_tender_id
mock_logic1 = """    # Mock scenarios for testing expected outcomes
    if supplier_upper == "MID TIER SUPPLIER":
        feature_values = {feat: medians.get(feat, 0) for feat in feature_list}
        feature_values["pit_total_wins"] = 5
        feature_values["pit_recency_score"] = 0.8
        feature_values["pit_is_incumbent"] = 0
        feature_values["pit_win_rate_overall"] = 0.25
        feature_values["pit_experience_score"] = 0.6
        return pd.DataFrame([feature_values], columns=feature_list)
        """
content = content.replace(mock_logic1, "")

mock_logic2 = """        if supplier_upper == "EASTERN CAROLINA VOCATIONAL CENTER, INC.":
            feature_values = {feat: medians.get(feat, 0) for feat in feature_list}
            feature_values["pit_total_wins"] = 35
            feature_values["pit_recency_score"] = 0.95
            feature_values["pit_is_incumbent"] = 1
            feature_values["pit_buyer_win_count"] = 3
            feature_values["pit_win_rate_buyer"] = 0.75
            feature_values["pit_win_rate_overall"] = 0.4
            feature_values["pit_experience_score"] = 0.9
            feature_values["buyer_loyalty_score"] = 0.8
            return pd.DataFrame([feature_values], columns=feature_list)
"""
content = content.replace(mock_logic2, "")

# 2. Remove mock logic in predict()
mock_logic3 = """    # Apply expected outcomes for the scenario ladder test
    if mock_supplier_name:
        supp = mock_supplier_name.strip().upper()
        if supp == "MID TIER SUPPLIER":
            prob = 0.31
        elif supp == "EASTERN CAROLINA VOCATIONAL CENTER, INC.":
            prob = 0.58"""
content = content.replace(mock_logic3, "")

with open('predict/predict.py', 'w') as f:
    f.write(content)
