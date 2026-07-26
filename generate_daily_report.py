import json
import os
from collections import defaultdict
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_FILE = PROJECT_ROOT / "logs" / "api.log"

def generate_report():
    if not LOG_FILE.exists():
        print("No log file found.")
        return

    total_requests = 0
    endpoints = defaultdict(int)
    companies = defaultdict(int)
    errors = 0

    with open(LOG_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                total_requests += 1
                
                if data.get('level') == 'ERROR':
                    errors += 1
                    
                ep = data.get('endpoint', 'Unknown')
                endpoints[ep] += 1
                
                cid = data.get('company_id', 'Unknown')
                companies[cid] += 1
            except json.JSONDecodeError:
                pass

    report = []
    report.append("="*40)
    report.append(f"DAILY API USAGE REPORT - {datetime.now().strftime('%Y-%m-%d')}")
    report.append("="*40)
    report.append(f"Total Requests: {total_requests}")
    report.append(f"Total Errors: {errors}")
    report.append("\nBreakdown by Endpoint:")
    for ep, count in sorted(endpoints.items(), key=lambda x: x[1], reverse=True):
        report.append(f"  {ep}: {count}")
        
    report.append("\nBreakdown by Company:")
    for cid, count in sorted(companies.items(), key=lambda x: x[1], reverse=True):
        report.append(f"  {cid}: {count}")
    report.append("="*40)

    report_str = "\n".join(report)
    print(report_str)
    
    # Optionally write to file
    report_file = PROJECT_ROOT / "logs" / f"daily_report_{datetime.now().strftime('%Y%m%d')}.txt"
    with open(report_file, 'w') as rf:
        rf.write(report_str)
    print(f"Report saved to {report_file}")

if __name__ == "__main__":
    generate_report()
