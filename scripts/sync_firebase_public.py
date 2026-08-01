"""
Mirror static/ -> firebase_public/ (the directory Firebase Hosting serves).

firebase.json sets "hosting": {"public": "firebase_public"}, so anything edited
only in static/ is invisible in production. The two trees were byte-identical
apart from the brand casing in a handful of user-visible strings, so this
copies static/ over and re-applies that casing rather than maintaining two
hand-edited copies.

Run after any UI change:
    python scripts/sync_firebase_public.py
"""

import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "static"
DST = ROOT / "firebase_public"

# The deployed build presents the brand upper-case in these user-visible spots.
CASING = [
    ("<title>CairoAI —", "<title>CAIROAI —"),
    ("CairoAI (Pty) Ltd", "CAIROAI (Pty) Ltd"),
    ("cName.textContent = 'CairoAI'", "cName.textContent = 'CAIROAI'"),
    ("// Default to CairoAI based on mock company info",
     "// Default to CAIROAI based on mock company info"),
]

COPY_EXT = {".html", ".css", ".js"}


def main() -> int:
    if not SRC.is_dir() or not DST.is_dir():
        print(f"missing {SRC} or {DST}", file=sys.stderr)
        return 1

    copied, transformed, skipped = 0, 0, 0
    for src_file in sorted(SRC.rglob("*")):
        if not src_file.is_file() or src_file.suffix.lower() not in COPY_EXT:
            continue
        rel = src_file.relative_to(SRC)
        dst_file = DST / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)

        if src_file.suffix.lower() == ".html":
            txt = src_file.read_text(encoding="utf-8")
            for a, b in CASING:
                txt = txt.replace(a, b)
            before = dst_file.read_text(encoding="utf-8") if dst_file.exists() else None
            if before != txt:
                dst_file.write_text(txt, encoding="utf-8", newline="\n")
                print(f"  html  {rel}")
                transformed += 1
            else:
                skipped += 1
        else:
            if dst_file.exists() and filecmp.cmp(src_file, dst_file, shallow=False):
                skipped += 1
                continue
            shutil.copy2(src_file, dst_file)
            print(f"  asset {rel}")
            copied += 1

    print(f"\nhtml updated: {transformed}   assets copied: {copied}   unchanged: {skipped}")

    # verify: every dst html differs from src ONLY by the casing rules
    print("\nverification (src vs dst, casing normalised):")
    ok = True
    for src_file in sorted(SRC.glob("*.html")):
        dst_file = DST / src_file.name
        if not dst_file.exists():
            print(f"  {src_file.name}: MISSING in firebase_public")
            ok = False
            continue
        a = src_file.read_text(encoding="utf-8")
        for x, y in CASING:
            a = a.replace(x, y)
        b = dst_file.read_text(encoding="utf-8")
        status = "match" if a == b else "DIFFERS"
        if a != b:
            ok = False
        print(f"  {src_file.name:18} {status}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
