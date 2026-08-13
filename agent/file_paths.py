"""
Central path resolution for files the app *generates* (quotation PDFs,
accreditation reports).

Same problem as agent/db_paths.py, different artefacts. Generated files were
written to repo-relative directories:

    os.makedirs("static/downloads", exist_ok=True)
    pdf.output(os.path.join("static", "downloads", filename))

The deployed function bundle is read-only, so on Cloud Functions that raises
OSError and the tool fails at the moment it tries to hand the user a PDF.

There was a second bug alongside it: quote_builder wrote to static/downloads and
returned "/static/downloads/<name>", while the endpoint that serves quotations
reads from data/generated_quotations. The two never pointed at the same place,
and the /static/ URL only resolved at all locally because StaticFiles is mounted
there. Everything generated now goes to one directory and is served by one
route, /api/generated/<name>.

    LIMITATION: as with the databases, /tmp is per-instance and ephemeral. A
    generated PDF is downloadable for as long as that instance lives, which
    covers the "generate it and click the link" flow but is not storage. Durable
    artefacts belong in Cloud Storage, which is separate work.
"""

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _on_serverless() -> bool:
    return bool(os.environ.get("K_SERVICE") or os.environ.get("FUNCTION_TARGET"))


def generated_dir() -> Path:
    """The directory generated files are written to and served from."""
    override = os.environ.get("AGENT_GENERATED_DIR")
    if override:
        root = Path(override)
    elif _on_serverless():
        root = Path("/tmp") / "dolo-generated"
    else:
        root = PROJECT_ROOT / "static" / "downloads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def pack_upload_dir(company_id: str, pack_id: str) -> Path:
    """
    Where an Autofill Vault pack's uploaded source documents live.

    Uploads, not generated artefacts, so they get their own root rather than
    being mixed into generated_dir() — everything in there is servable through
    /api/generated/<name> once registered, and a raw uploaded tender pack is not
    something the download route should ever be asked about.

    Same read-only-filesystem constraint as everything else here: on Cloud
    Functions this must be under /tmp or the very first upload raises OSError.
    And the same limitation applies — /tmp is per-instance, so a pack whose
    files were uploaded to one instance and submitted against another will find
    them gone. The pack row records that as an error rather than silently
    producing an empty draft.

    company_id and pack_id are both segment-sanitised: company_id comes from the
    authenticated principal and pack_id is a uuid4 we minted, but a path built
    from identifiers is worth making structurally incapable of traversal rather
    than relying on where they came from.
    """
    override = os.environ.get("AGENT_UPLOAD_DIR")
    if override:
        root = Path(override)
    elif _on_serverless():
        root = Path("/tmp") / "dolo-uploads"
    else:
        root = PROJECT_ROOT / "data" / "autofill_packs"

    target = root / _path_segment(company_id) / _path_segment(pack_id)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _path_segment(value: str) -> str:
    """One directory name, with anything that could leave the directory removed."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._-")
    return cleaned or "unknown"


def safe_generated_path(filename: str) -> Path:
    """
    Resolve a caller-supplied filename inside generated_dir().

    The filename reaches this from a URL path segment, so it is untrusted:
    strip it to a bare name and confirm the result is still inside the
    directory before handing back a path to read.
    """
    name = os.path.basename(filename)
    if not name or name in (".", "..") or not re.fullmatch(r"[A-Za-z0-9._\-]+", name):
        raise ValueError(f"unsafe filename: {filename!r}")

    root = generated_dir().resolve()
    target = (root / name).resolve()
    if target != root / name or root not in target.parents:
        raise ValueError(f"path escapes generated dir: {filename!r}")
    return target


def public_url(filename: str) -> str:
    """The URL the frontend should use to download a generated file."""
    return f"/api/generated/{os.path.basename(filename)}"
