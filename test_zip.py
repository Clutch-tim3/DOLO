import zipfile
try:
    with zipfile.ZipFile("tests/fixtures/alfred_duma.pdf") as docx:
        print("Success")
except Exception as e:
    print("Exception:", e)
