"""
Durable storage for the files this app generates.

WHY THIS EXISTS
---------------
`agent/file_paths.py` writes generated documents to `/tmp/dolo-generated` on
Cloud Run, and its own docstring names the consequence:

    LIMITATION: /tmp is per-instance and ephemeral. A generated PDF is
    downloadable for as long as that instance lives ... Durable artefacts
    belong in Cloud Storage, which is separate work.

This is that work, and the limitation was not theoretical. A filled SBD 1
produced in production downloaded fine seconds after it was made and returned
"File not found or expired" ten minutes later. The ownership row was still in
Cloud SQL — the database is durable — and the PDF behind it was gone, because
a different instance served the second request.

That is the whole deliverable of Agent Autofill disappearing between being
made and being fetched.

HOW IT WORKS
------------
The local directory stays exactly as it is, and stays the write target: the
fill engines hand PyMuPDF and python-docx a real filesystem path, and changing
that would mean rewriting every generator. Instead a file is mirrored to the
bucket when it is registered, and restored from the bucket if the local copy
has vanished by the time someone asks for it. `/tmp` becomes a cache in front
of durable storage rather than the storage itself.

FAILING SOFT, IN BOTH DIRECTIONS
--------------------------------
Nothing here raises. An upload that fails leaves the file exactly as it is
today — present on one instance, and better than a generator that crashes
after doing the work. A restore that fails leaves the route to 404, which is
what it already does.

The one thing this must never do is affect who may read a file. The download
route checks ownership BEFORE asking for a restore, so a caller who does not
own a document cannot cause it to be fetched out of the bucket.

NOT CONFIGURED IS A NORMAL STATE
--------------------------------
Locally there is no bucket and no credentials, generated files live under
`static/downloads`, and that directory is durable because the machine is. So
`enabled()` is False, every function is a no-op, and nothing about local
development changes.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("agent.object_store")

#: Explicit bucket override. Without it the bucket is taken from the Firebase
#: config the runtime already injects, so a correctly deployed service needs no
#: extra configuration.
BUCKET_ENV = "GENERATED_FILES_BUCKET"

#: Prefix inside the bucket. Keeps generated documents from colliding with
#: anything else the project stores there.
OBJECT_PREFIX = "generated/"

_client = None
_client_pid: int | None = None


def bucket_name() -> str | None:
    """
    Which bucket to use, or None when there is not one.

    `FIREBASE_CONFIG` is set by the Functions runtime and carries the default
    bucket, e.g. {"projectId": "cairoai", "storageBucket": "cairoai.firebase
    storage.app"}. Reading it means production works without a second setting
    that could drift from the first.
    """
    explicit = (os.environ.get(BUCKET_ENV) or "").strip()
    if explicit:
        return explicit

    raw = os.environ.get("FIREBASE_CONFIG") or ""
    if not raw:
        return None
    try:
        return (json.loads(raw).get("storageBucket") or "").strip() or None
    except (ValueError, AttributeError):
        logger.warning("FIREBASE_CONFIG is not readable JSON; no bucket configured")
        return None


def enabled() -> bool:
    """Whether durable storage is available here."""
    if not bucket_name():
        return False
    try:
        from google.cloud import storage  # noqa: F401
    except ImportError:
        logger.warning("google-cloud-storage is not installed; generated files "
                       "stay on local disk only")
        return False
    return True


def _bucket():
    """
    The bucket handle, cached per process.

    PID-guarded for the same reason the Cloud SQL connector and the ASGI bridge
    are: a client built before a fork carries connections the child cannot
    safely use.
    """
    global _client, _client_pid

    from google.cloud import storage

    if _client is None or _client_pid != os.getpid():
        _client = storage.Client()
        _client_pid = os.getpid()
    return _client.bucket(bucket_name())


def _blob_name(filename: str) -> str:
    return f"{OBJECT_PREFIX}{os.path.basename(filename)}"


def upload(local_path, filename: str | None = None) -> bool:
    """
    Mirror a generated file into the bucket. Returns whether it got there.

    Never raises. A generator that has just spent a Vision call and a model
    call producing a document must not lose it to a storage error — the file is
    still on this instance, which is exactly where it would have been before
    any of this existed.
    """
    if not enabled():
        return False

    local_path = Path(local_path)
    name = filename or local_path.name
    if not local_path.exists():
        logger.warning("object_store: nothing to upload at %s", local_path)
        return False

    try:
        _bucket().blob(_blob_name(name)).upload_from_filename(str(local_path))
        logger.info("object_store: uploaded %s (%d bytes)", name,
                    local_path.stat().st_size)
        return True
    except Exception:  # noqa: BLE001 - storage failure must not lose the file
        logger.exception("object_store: could not upload %s", name)
        return False


def ensure_local(filename: str, local_path) -> bool:
    """
    Make sure `local_path` exists, restoring it from the bucket if it does not.

    Returns whether the file is there afterwards. The common case is that the
    local copy is present and this does nothing at all; the interesting case is
    a cold instance serving a link made on another one.

    The caller must have already established that this file may be read by
    whoever is asking. This restores whatever name it is given.
    """
    local_path = Path(local_path)
    if local_path.exists():
        return True
    if not enabled():
        return False

    try:
        blob = _bucket().blob(_blob_name(filename))
        if not blob.exists():
            logger.info("object_store: %s is not in the bucket either", filename)
            return False
        local_path.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(local_path))
        logger.info("object_store: restored %s from the bucket", filename)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("object_store: could not restore %s", filename)
        return False


def delete(filename: str) -> bool:
    """Remove a generated file from the bucket. Never raises."""
    if not enabled():
        return False
    try:
        _bucket().blob(_blob_name(filename)).delete()
        return True
    except Exception:  # noqa: BLE001
        logger.warning("object_store: could not delete %s", filename, exc_info=True)
        return False
