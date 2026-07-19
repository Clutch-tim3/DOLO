import re
import json

with open("static/index.html") as f:
    html = f.read()

# Find all script contents
scripts = re.findall(r"<script>([\s\S]*?)</script>", html)
for i, script in enumerate(scripts):
    with open(f"script_{i}.js", "w") as out:
        out.write(script)
    print(f"Saved script_{i}.js")

