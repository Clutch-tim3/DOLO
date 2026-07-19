import requests
import time
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

files = [
    ('files', ('alfred_duma.pdf', open('tests/fixtures/alfred_duma.pdf', 'rb'), 'application/pdf')),
    ('files', ('lv_cabling_tender.pdf', open('tests/fixtures/lv_cabling_tender.pdf', 'rb'), 'application/pdf')),
    ('files', ('rfb_001_comms.docx', open('tests/fixtures/rfb_001_comms.docx', 'rb'), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'))
]

data = {
    'supplier_name': 'TEST SUPPLIER',
    'bbbee_level': 1
}

print("Submitting batch job...")
response = requests.post('http://127.0.0.1:5000/api/batch-sort', files=files, data=data)
res_json = response.json()
job_id = res_json.get('job_id')
print(f"Job ID: {job_id}")
if not job_id:
    print(f"Error: {res_json}")
    exit(1)

while True:
    res = requests.get(f'http://127.0.0.1:5000/api/batch-status/{job_id}').json()
    if res['status'] == 'completed':
        print("\n--- BATCH RESULTS ---")
        for r in res['results']:
            prob = f"{r['sa_adjusted_probability']*100:.1f}%" if r.get('sa_adjusted_probability') else "N/A"
            print(f"File: {r['filename']:<25} | Rec: {r['recommendation']:<12} | Prob: {prob:<6} | Pos: {str(r.get('competitive_position')):<8} | Err: {r.get('processing_error')}")
        break
    time.sleep(1)
