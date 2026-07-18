import sys
import re

with open('app.py', 'r') as f:
    app_content = f.read()

# Let's add inject_parsed_features to app.py
inject_function = """
def inject_parsed_features(features_df, parsed_tender):
    if parsed_tender:
        if 'deadline_days' in parsed_tender:
            features_df['deadline_days'] = parsed_tender['deadline_days']
        if 'tender_proceduretype' in parsed_tender:
            features_df['tender_proceduretype'] = parsed_tender['tender_proceduretype']
        if 'tender_supplytype' in parsed_tender:
            features_df['tender_supplytype'] = parsed_tender['tender_supplytype']
        if 'tender_estimatedpriceUsd' in parsed_tender:
            features_df['tender_estimatedpriceUsd'] = parsed_tender['tender_estimatedpriceUsd']
        if 'bid_priceUsd' in parsed_tender:
            features_df['bid_priceUsd'] = parsed_tender['bid_priceUsd']
        if 'tender_description_length' in parsed_tender:
            features_df['tender_description_length'] = parsed_tender['tender_description_length']
        if 'functionality_threshold_pct' in parsed_tender:
            features_df['had_functionality_gate'] = parsed_tender['had_functionality_gate']
            features_df['functionality_threshold_pct'] = parsed_tender['functionality_threshold_pct']
    return features_df

"""

if "def inject_parsed_features(" not in app_content:
    # Insert it before process_batch_job
    idx = app_content.find("def process_batch_job(")
    app_content = app_content[:idx] + inject_function + app_content[idx:]

# In batch processing
batch_extraction_logic = """        if parsed_tender.get("extraction_incomplete"):
            job["processed"] += 1
            job["results"].append({
                "prediction_id": str(uuid.uuid4()),
                "filename": filename,
                "tender_identifier": tender_id,
                "disqualified": False,
                "hard_failures": [],
                "win_probability": None,
                "sa_adjusted_probability": None,
                "recommendation": "ERROR",
                "competitive_position": None,
                "parsed_tender_value": None,
                "preferential_framework": parsed_tender.get("evaluation_system", "Unknown"),
                "processing_error": "Failed to extract required financial parameters (tender_value, bid_price) from document."
            })
            continue"""

new_batch_extraction_logic = """        if parsed_tender.get("extraction_incomplete"):
            msg = f"Extraction Completeness [{int(parsed_tender.get('extraction_completeness', 0) * 100)}%]: Missing {', '.join(parsed_tender.get('missing_fields', []))}"
            job["processed"] += 1
            job["results"].append({
                "prediction_id": str(uuid.uuid4()),
                "filename": filename,
                "tender_identifier": tender_id,
                "disqualified": False,
                "hard_failures": [],
                "win_probability": None,
                "sa_adjusted_probability": None,
                "recommendation": "ERROR",
                "competitive_position": None,
                "parsed_tender_value": None,
                "preferential_framework": parsed_tender.get("evaluation_system", "Unknown"),
                "extraction_completeness": parsed_tender.get('extraction_completeness', 0),
                "processing_error": msg
            })
            continue"""
app_content = app_content.replace(batch_extraction_logic, new_batch_extraction_logic)

# In batch processing ML features
old_ml_build = """            features_df = extract_features_from_tender_id(
                tender_id, name_to_use, feature_list, artifacts["medians"]
            )
            features_df = build_new_features(features_df, artifacts["medians"])
            features_df = encode_and_impute(
                features_df, artifacts["encoder"], artifacts["cat_cols"], artifacts["medians"]
            )"""

new_ml_build = """            features_df = extract_features_from_tender_id(
                tender_id, name_to_use, feature_list, artifacts["medians"]
            )
            features_df = build_new_features(features_df, artifacts["medians"])
            features_df = inject_parsed_features(features_df, parsed_tender)
            features_df = encode_and_impute(
                features_df, artifacts["encoder"], artifacts["cat_cols"], artifacts["medians"]
            )"""
app_content = app_content.replace(old_ml_build, new_ml_build)

# Ensure batch appending contains completeness
old_append = """                "competitive_position": sa_res["competitive_position"],
                "parsed_tender_value": tender_value,
                "preferential_framework": eval_system
            })"""
new_append = """                "competitive_position": sa_res["competitive_position"],
                "parsed_tender_value": tender_value,
                "preferential_framework": eval_system,
                "extraction_completeness": parsed_tender.get('extraction_completeness', 1.0)
            })"""
app_content = app_content.replace(old_append, new_append)

# In single endpoint
single_extraction = """    if parsed_tender.get("extraction_incomplete"):
        raise HTTPException(status_code=400, detail="Failed to extract required financial parameters (tender_value, bid_price) from document.")"""
new_single_extraction = """    if parsed_tender.get("extraction_incomplete"):
        msg = f"Extraction Completeness [{int(parsed_tender.get('extraction_completeness', 0) * 100)}%]: Missing {', '.join(parsed_tender.get('missing_fields', []))}"
        raise HTTPException(status_code=400, detail=msg)"""
app_content = app_content.replace(single_extraction, new_single_extraction)

single_ml_build = """        features_df = extract_features_from_tender_id(
            tender_id, name_to_use, feature_list, artifacts["medians"]
        )
        features_df = build_new_features(features_df, artifacts["medians"])
        features_df = encode_and_impute(
            features_df, artifacts["encoder"], artifacts["cat_cols"], artifacts["medians"]
        )"""
new_single_ml_build = """        features_df = extract_features_from_tender_id(
            tender_id, name_to_use, feature_list, artifacts["medians"]
        )
        features_df = build_new_features(features_df, artifacts["medians"])
        features_df = inject_parsed_features(features_df, parsed_tender)
        features_df = encode_and_impute(
            features_df, artifacts["encoder"], artifacts["cat_cols"], artifacts["medians"]
        )"""
app_content = app_content.replace(single_ml_build, new_single_ml_build)

with open('app.py', 'w') as f:
    f.write(app_content)
