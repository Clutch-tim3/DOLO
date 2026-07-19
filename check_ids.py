from bs4 import BeautifulSoup
import re

with open('static/index.html') as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')

script = soup.find('script').string
if not script:
    print("Script not found!")
    exit(1)

# Find all document.getElementById("...") or document.getElementById('...')
ids = re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", script)

missing = []
for id_val in ids:
    if not soup.find(id=id_val):
        missing.append(id_val)

if missing:
    print("MISSING IDs IN HTML:", set(missing))
else:
    print("All IDs are present in the HTML.")
