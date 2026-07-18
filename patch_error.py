import sys
from pathlib import Path

app_path = Path("/Users/harry/Documents/Data set V2/tender_ml/app.py")
content = app_path.read_text()

import_tb = "import traceback\n"
if "import traceback" not in content:
    content = import_tb + content

old_code = """
        except Exception as err:
            job["results"].append({
"""

new_code = """
        except Exception as err:
            import traceback
            traceback.print_exc()
            job["results"].append({
"""

content = content.replace(old_code, new_code)
app_path.write_text(content)
