import sys

with open('app.py', 'r') as f:
    content = f.read()

# 1. Patch `api_tender_submit`
# Find `return { "supplier": name_to_use,`
return_block = """return {
            "supplier": name_to_use,
            "matched_from_archive": matched_company is not None,
            "registration_number": matched_company.get("registration_number", "Pending") if matched_company else "Pending",
            "bbbee_level": bbbee_to_use,
            "win_probability": final_probability,
            "base_probability": base_prob,
            "recommendation": recommendation,
            "confidence": confidence,
            "threshold": threshold,
            "sa_analysis": sa_analysis
        }"""

new_return_block = """prediction_id = str(uuid.uuid4())
        
        # Save to tracked_outcomes
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        now = datetime.now().isoformat()
        c.execute('''INSERT INTO tracked_outcomes (id, prediction_id, tender_identifier, filename, supplier_name, predicted_probability, sa_adjusted_probability, recommendation, actual_outcome, outcome_date, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                    (str(uuid.uuid4()), prediction_id, tender_id, tender_file.filename if tender_file else "", name_to_use, base_prob, final_probability, recommendation, "pending", None, "", now, now))
        conn.commit()
        conn.close()

        return {
            "prediction_id": prediction_id,
            "supplier": name_to_use,
            "matched_from_archive": matched_company is not None,
            "registration_number": matched_company.get("registration_number", "Pending") if matched_company else "Pending",
            "bbbee_level": bbbee_to_use,
            "win_probability": final_probability,
            "base_probability": base_prob,
            "recommendation": recommendation,
            "confidence": confidence,
            "threshold": threshold,
            "sa_analysis": sa_analysis
        }"""
content = content.replace(return_block, new_return_block)

# 2. Patch `process_batch_job` for successful predictions
job_append = """job["results"].append({
                "filename": filename,
                "tender_identifier": tender_id,
                "disqualified": False,
                "hard_failures": [],
                "win_probability": base_prob,
                "sa_adjusted_probability": final_probability,
                "recommendation": recommendation,
                "competitive_position": sa_score["competitive_position"],
                "parsed_tender_value": tender_value,
                "preferential_framework": sa_score["evaluation_system"],
                "processing_error": None
            })"""

new_job_append = """prediction_id = str(uuid.uuid4())
            conn = sqlite3.connect(str(DB_PATH))
            c = conn.cursor()
            now = datetime.now().isoformat()
            c.execute('''INSERT INTO tracked_outcomes (id, prediction_id, tender_identifier, filename, supplier_name, predicted_probability, sa_adjusted_probability, recommendation, actual_outcome, outcome_date, notes, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                        (str(uuid.uuid4()), prediction_id, tender_id, filename, name_to_use, base_prob, final_probability, recommendation, "pending", None, "", now, now))
            conn.commit()
            conn.close()

            job["results"].append({
                "prediction_id": prediction_id,
                "filename": filename,
                "tender_identifier": tender_id,
                "disqualified": False,
                "hard_failures": [],
                "win_probability": base_prob,
                "sa_adjusted_probability": final_probability,
                "recommendation": recommendation,
                "competitive_position": sa_score["competitive_position"],
                "parsed_tender_value": tender_value,
                "preferential_framework": sa_score["evaluation_system"],
                "processing_error": None
            })"""
content = content.replace(job_append, new_job_append)

with open('app.py', 'w') as f:
    f.write(content)
