import requests

url = "http://127.0.0.1:5000/api/batch-sort"
files = [
    ("files", ("RFB 001 National Communication.docx", open("tests/fixtures/rfb_001_comms.docx", "rb"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
]
data = {"supplier_name": "DONINGTON VALE"}

res = requests.post(url, files=files, data=data)
job_id = res.json().get("job_id")

import time
time.sleep(2)
res2 = requests.get(f"http://127.0.0.1:5000/api/batch-status/{job_id}")
print(res2.json())
