"""
A logo can be uploaded, survives a cold start, and reaches the quotation.

`logo_file_path` existed on the profile and quote_document.py drew it, and
nothing anywhere set it. The only way to get a logo onto a quotation was to
write a filesystem path into the database by hand — and on Cloud Run that path
is per-instance and vanishes.

Two things are asserted beyond "it uploads":

  - the file is an image BY CONTENT, not by name. This codebase already learned
    that lesson from seven fixtures named .docx that are OLE2 .doc. A name is a
    claim made by whoever is uploading, and the bytes end up in a PDF renderer.

  - the profile stores a bare FILENAME, not an absolute path. A path recorded
    on one instance means nothing on the next, which is the whole reason the
    hand-written approach did not work.
"""

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from agent import auth, file_paths
from agent.memory import company_store
from agent.quotation import quote_document
from app import MAX_LOGO_BYTES, app

client = TestClient(app)

# A real 1x1 PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64


@pytest.fixture
def account():
    company_id = f"logo-{uuid.uuid4().hex[:10]}"
    user = auth.create_user(f"logo-{uuid.uuid4().hex[:8]}@example.test", company_id,
                            "logo-test-not-a-real-password")
    yield {"Authorization": f"Bearer {auth.issue_session(user)}"}, company_id
    company_store.delete_company_profile(company_id)


# --- it works -----------------------------------------------------------------

def test_a_logo_can_be_uploaded_and_is_recorded(account):
    headers, company_id = account

    r = client.post("/api/company-profile/logo", headers=headers,
                    files={"logo": ("brand.png", PNG, "image/png")})

    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "png"

    stored = company_store.get_company_profile(company_id)["logo_file_path"]
    assert stored, "the profile does not record the logo"
    assert stored == r.json()["filename"]


def test_the_profile_stores_a_filename_not_a_path(account):
    """
    An absolute path is what made the hand-written approach fail: it points at
    a directory that is wiped on the next cold start.
    """
    headers, company_id = account
    client.post("/api/company-profile/logo", headers=headers,
                files={"logo": ("brand.png", PNG, "image/png")})

    stored = company_store.get_company_profile(company_id)["logo_file_path"]
    assert not Path(stored).is_absolute()
    assert os.sep not in stored and "/" not in stored


@pytest.mark.parametrize("name,data,kind", [
    ("a.png", PNG, "png"), ("a.jpg", JPEG, "jpg"),
    ("a.gif", GIF, "gif"), ("a.webp", WEBP, "webp"),
])
def test_the_usual_formats_are_accepted(account, name, data, kind):
    headers, _ = account
    r = client.post("/api/company-profile/logo", headers=headers,
                    files={"logo": (name, data, "application/octet-stream")})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == kind


# --- what it refuses ----------------------------------------------------------

def test_a_file_that_is_not_an_image_is_refused_however_it_is_named(account):
    """
    The name says PNG; the bytes are a PDF. Detecting by extension would store
    it and hand it to the PDF renderer.
    """
    headers, company_id = account
    r = client.post("/api/company-profile/logo", headers=headers,
                    files={"logo": ("brand.png", b"%PDF-1.7\n%not an image",
                                    "image/png")})

    assert r.status_code == 400
    assert "not an image" in r.json()["detail"].lower()
    assert company_store.get_company_profile(company_id).get("logo_file_path") in (None, "")


def test_a_script_disguised_as_an_image_is_refused(account):
    headers, _ = account
    r = client.post("/api/company-profile/logo", headers=headers,
                    files={"logo": ("logo.png", b"<?php system($_GET['c']); ?>",
                                    "image/png")})
    assert r.status_code == 400


def test_an_oversized_upload_is_refused(account):
    """An upload route with no cap is a way to fill the disk."""
    headers, _ = account
    too_big = PNG + b"\x00" * (MAX_LOGO_BYTES + 1)
    r = client.post("/api/company-profile/logo", headers=headers,
                    files={"logo": ("big.png", too_big, "image/png")})
    assert r.status_code == 400
    assert "limit" in r.json()["detail"].lower()


def test_an_empty_file_is_refused(account):
    headers, _ = account
    r = client.post("/api/company-profile/logo", headers=headers,
                    files={"logo": ("empty.png", b"", "image/png")})
    assert r.status_code == 400


def test_an_anonymous_upload_is_refused():
    r = client.post("/api/company-profile/logo",
                    files={"logo": ("brand.png", PNG, "image/png")})
    assert r.status_code == 401


# --- it reaches the document --------------------------------------------------

def test_the_renderer_resolves_the_stored_filename(account):
    headers, company_id = account
    client.post("/api/company-profile/logo", headers=headers,
                files={"logo": ("brand.png", PNG, "image/png")})

    stored = company_store.get_company_profile(company_id)["logo_file_path"]
    resolved = quote_document._resolve_logo(stored)

    assert resolved is not None, "the renderer cannot find the uploaded logo"
    assert resolved.exists()
    assert resolved.read_bytes() == PNG


def test_a_missing_logo_does_not_break_a_quotation():
    """A quotation must never fail because of its letterhead."""
    assert quote_document._resolve_logo(None) is None
    assert quote_document._resolve_logo("") is None
    assert quote_document._resolve_logo("was-never-uploaded.png") is None


def test_an_absolute_path_from_an_older_profile_still_works(tmp_path):
    """
    Profiles written before upload existed hold absolute paths. A quotation
    must not lose its letterhead to this change.
    """
    legacy = tmp_path / "old_logo.png"
    legacy.write_bytes(PNG)
    assert quote_document._resolve_logo(str(legacy)) == legacy
    assert quote_document._resolve_logo(str(tmp_path / "gone.png")) is None
