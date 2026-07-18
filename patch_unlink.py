from pathlib import Path
p = Path("/Users/harry/Documents/Data set V2/tender_ml/app.py")
txt = p.read_text()
txt = txt.replace("if Path(path).exists(): Path(path).unlink()", "# if Path(path).exists(): Path(path).unlink()")
p.write_text(txt)
