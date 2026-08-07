"""
Reading legacy Word .doc files (OLE2 binary format).

python-docx only handles .docx (a ZIP). The seven National Treasury forms in
tests/fixtures were .doc, and renaming them to .docx does not convert them —
the bytes still start with the OLE2 magic number, which is exactly what a user
will do and then report as "your app is broken". So format is detected by
SIGNATURE, never by file extension.

What this module can and cannot do, stated plainly because the limit matters:

  READ  — yes, via `antiword`, which renders tables as pipe-delimited text. That
          is enough to extract labels and detect which cells are blank.
  WRITE — no. There is no pure-Python writer for the binary .doc format, so a
          .doc form cannot be filled in place. The orchestrator must tell the
          user to save it as .docx rather than silently producing nothing.

If antiword is missing, this degrades to a clear error rather than an empty
result, so a missing binary can never look like a form with no fields in it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: OLE2 compound-document header — legacy .doc, .xls, .ppt all start with it.
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
#: ZIP header — .docx and every other OOXML file.
ZIP_MAGIC = b"PK\x03\x04"


class LegacyDocError(RuntimeError):
    """Raised when a .doc exists but cannot be read."""


def detect_format(path: str | Path) -> str:
    """
    Return "docx", "doc", "pdf" or "unknown" from the file's magic bytes.

    Deliberately ignores the extension. A .doc renamed to .docx is the common
    case, and trusting the name produces a confusing PackageNotFoundError deep
    inside python-docx instead of an actionable message.
    """
    head = Path(path).open("rb").read(8)
    if head.startswith(ZIP_MAGIC):
        return "docx"
    if head.startswith(OLE2_MAGIC):
        return "doc"
    if head.startswith(b"%PDF"):
        return "pdf"
    return "unknown"


def antiword_available() -> bool:
    return shutil.which("antiword") is not None


@dataclass
class LegacyRow:
    cells: list[str] = field(default_factory=list)


@dataclass
class LegacyDoc:
    path: str
    text: str
    rows: list[LegacyRow] = field(default_factory=list)

    @property
    def writable(self) -> bool:
        """Legacy .doc can be read but never written. Always False."""
        return False


def _parse_pipe_table(line: str) -> list[str] | None:
    """antiword renders table rows as '|cell |cell |'. Anything else is prose."""
    if not line.startswith("|"):
        return None
    parts = [c.strip() for c in line.strip().strip("|").split("|")]
    return parts if parts else None


def read_doc(path: str | Path) -> LegacyDoc:
    """
    Extract text and table rows from a legacy .doc.

    Raises LegacyDocError rather than returning empty, so "cannot read" is never
    mistaken for "no fields found".
    """
    path = Path(path)
    if not path.exists():
        raise LegacyDocError(f"File not found: {path}")

    fmt = detect_format(path)
    if fmt != "doc":
        raise LegacyDocError(
            f"{path.name} is not a legacy .doc (detected: {fmt}). "
            "Use the .docx reader instead."
        )
    if not antiword_available():
        raise LegacyDocError(
            "antiword is not installed, so legacy .doc files cannot be read. "
            "Ask the user to save the document as .docx (Word: Save As, then "
            "change the file type dropdown to 'Word Document (.docx)' — "
            "renaming the file is not enough)."
        )

    try:
        proc = subprocess.run(
            ["antiword", "-w", "0", str(path)],
            capture_output=True, timeout=60,
        )
    except subprocess.TimeoutExpired as e:
        raise LegacyDocError(f"antiword timed out reading {path.name}") from e

    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()[:200]
        raise LegacyDocError(f"antiword failed on {path.name}: {detail}")

    text = (proc.stdout or b"").decode("utf-8", "replace")
    rows = [LegacyRow(cells) for line in text.splitlines()
            if (cells := _parse_pipe_table(line))]
    return LegacyDoc(str(path), text, rows)


def iter_label_blank_pairs(doc: LegacyDoc):
    """
    Yield (label, location) for cells that look like a label with an empty
    neighbour — the same shape the .docx filler looks for, so a legacy form can
    at least be ANALYSED and reported on even though it cannot be written.
    """
    for r_i, row in enumerate(doc.rows, start=1):
        for c_i in range(len(row.cells) - 1):
            label, nxt = row.cells[c_i], row.cells[c_i + 1]
            if not label or len(label) < 3:
                continue
            if nxt:
                continue
            if not re.search(r"[A-Za-z]", label):
                continue
            yield label, f"row {r_i}, col {c_i + 1}"
