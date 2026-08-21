"""
Extracting a long document must not hold the whole document in memory.

THE PRODUCTION FAILURE

The owner: "im getting a server refused error frequently when i submit certain
tender packs" — HTTP 503.

Every 503 in the Cloud Run log is `/api/autofill-packs/<id>/submit`, and every
one is paired within seconds by:

    Memory limit of 1024 MiB exceeded with 1101 MiB used
    ...the container instance was found to be using too much memory and was
    terminated.

pdfplumber caches every derived object it builds for a page — chars, words,
edges, table output — and keeps them for the life of the PDF object. Nothing in
extraction needs a page once its blanks are collected, so on a long document the
cache is pure growth. Measured on the owner's real 145-page pack:

    before   573 MB peak, 195 MB retained     ONE document
    after     64 MB peak

A pack holds several documents, so submitting one went past 1024 MiB, Cloud Run
killed the container mid-request, and the browser got a 503 with nothing to
explain it.

WHY THE SUITE NEVER SAW IT

Every extraction test uses a one- or two-page fixture, where the cache costs
nothing. The failure needs length, and it only shows up as a crash when
something else imposes a ceiling. Nothing in the suite imposed one.
"""

import ast
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_autofill.extraction import layout_blank_extractor as extractor

PACK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "archive", "temp_tender_BID_DOCUMENT_06FY27_.pdf")

#: Generous. The point is to catch "holds every page", which was 9x this, not to
#: police ordinary allocation.
MAX_GROWTH_MB = 250


def test_every_page_is_released_after_it_is_read():
    """
    Structural, so it holds without the 145-page fixture. The per-page loop
    must call `flush_cache` — a page kept after its blanks are collected is a
    page held until the document closes.
    """
    tree = ast.parse(inspect.getsource(extractor.extract_pdf_blanks))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "flush_cache"]
    assert calls, (
        "extract_pdf_blanks does not release pages; a long pack will grow "
        "until Cloud Run kills the container and the user sees a 503")


def test_freeing_memory_can_never_fail_a_fill():
    """
    A cache flush that raised would turn a memory optimisation into a lost
    draft. It is guarded.
    """
    tree = ast.parse(inspect.getsource(extractor.extract_pdf_blanks))
    guarded = any(
        isinstance(call.func, ast.Attribute) and call.func.attr == "flush_cache"
        for node in ast.walk(tree) if isinstance(node, ast.Try)
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
    )
    assert guarded, "flush_cache must be inside a try block"


@pytest.mark.skipif(not os.path.exists(PACK), reason="the 145-page pack is not present")
def test_a_long_document_does_not_accumulate():
    """
    The measurement itself, against the document that caused the outage.

    Skipped where psutil is unavailable rather than failed: this is a resource
    assertion, and a missing measuring tool is not a regression.
    """
    psutil = pytest.importorskip("psutil")
    import gc

    process = psutil.Process()
    gc.collect()
    before = process.memory_info().rss / 1048576

    blanks = extractor.extract_pdf_blanks(PACK)

    peak = process.memory_info().rss / 1048576
    growth = peak - before

    assert blanks, "the pack still has to yield blanks"
    assert growth < MAX_GROWTH_MB, (
        f"extraction grew {growth:.0f} MB on one document. The Cloud Run "
        f"function has 2048 MiB and a pack holds several documents; this is "
        f"how submitting one returned 503."
    )


@pytest.mark.skipif(not os.path.exists(PACK), reason="the 145-page pack is not present")
def test_releasing_pages_does_not_change_what_is_found():
    """
    A memory fix that quietly dropped fields would be worse than the crash. The
    same document must yield the same blanks.
    """
    blanks = extractor.extract_pdf_blanks(PACK)
    assert len(blanks) > 300, f"only {len(blanks)} blanks; extraction lost work"
    assert any(b.label_text for b in blanks), "labels went missing"


# --- the other production 500 ---------------------------------------------------

def test_blob_becomes_bytea_on_postgres():
    """
    `/api/autofill/providers/status` returned 500 on EVERY request in
    production — `type "blob" does not exist`. Postgres has no BLOB, and
    `provider_tokens.token_ciphertext BLOB NOT NULL` is run by
    `provider_db.ensure_schema` on every call to that endpoint.

    The suite stayed green because it runs on SQLite, which accepts BLOB.
    """
    import agent.db as db

    original = db.is_postgres
    db.is_postgres = lambda: True
    try:
        out = db.translate("CREATE TABLE t (token_ciphertext BLOB NOT NULL)")
    finally:
        db.is_postgres = original

    assert "BYTEA" in out.upper()
    assert "BLOB" not in out.upper()


def test_blob_is_untouched_on_sqlite():
    """SQLite is the local and test backend and takes BLOB as it is."""
    import agent.db as db

    original = db.is_postgres
    db.is_postgres = lambda: False
    try:
        out = db.translate("CREATE TABLE t (token_ciphertext BLOB NOT NULL)")
    finally:
        db.is_postgres = original

    assert "BLOB" in out.upper()
