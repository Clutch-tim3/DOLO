import re

with open("app.py", "r", encoding="utf-8") as f:
    code = f.read()

if "predict_tender_region" not in code:
    import_line = "from predict.regional_router import predict_tender_region, detect_region\n"
    code = import_line + code

status_endpoint = '''
@app.get("/api/model-status")
async def api_model_status():
    """Returns active status and metrics of specialized regional engines (Conquest-ZA and Conquest-UK)."""
    return {
        "active_engines": {
            "Conquest-ZA": {
                "region": "South Africa (ZA)",
                "framework": "PPPFA 80/20 & 90/10",
                "auc_val": 0.857833,
                "auc_test": 0.857833,
                "status": "LOCKED_PRODUCTION_BASELINE"
            },
            "Conquest-UK": {
                "region": "United Kingdom (GB)",
                "framework": "MEAT PCR 2015",
                "auc_val": 0.694060,
                "auc_test": 0.694060,
                "status": "STANDALONE_REGIONAL_BASELINE"
            }
        }
    }
'''

if "/api/model-status" not in code:
    idx = code.find('@app.get("/workspace")')
    if idx != -1:
        code = code[:idx] + status_endpoint + "\n" + code[idx:]

with open("app.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Patched app.py with regional model status endpoint.")
