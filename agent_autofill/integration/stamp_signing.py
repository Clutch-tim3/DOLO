"""
Tamper-evidence for the review gate.

Two different forgeries were demonstrated against the export gate, and they
need two different defences — one MAC does not cover both.

  1. A direct write to `agent_memory.db` setting `acknowledged_at` on
     `autofill_review_item` rows. Signing the *stamp* does not help here: the
     export path would read the forged rows, believe them, and sign the result
     itself. The defence has to sit on the acknowledgement record, so each one
     carries a MAC that only the acknowledgement path can produce. A row whose
     `acknowledged_at` was written by hand has no valid MAC.

  2. Calling `stamp_docx()` directly with fabricated counts. `ReviewStamp`
     refuses REVIEWED-with-open-flags, but a caller passing `flags_open=0` got
     a real REVIEWED file while the database still said DRAFT. Nothing tied the
     file to the review record. The stamp MAC ties them.

The secret comes from the environment, the same route `ANTHROPIC_API_KEY`
takes — Secret Manager in production, `.env.local` for local dev (see CLAUDE.md
trap 3: it must never go in `.env`). If it is absent, signing and verification
both raise. That is deliberate: an unsigned export is exactly the artefact
these MACs exist to make impossible, so the failure mode is "no export", never
"export without tamper-evidence".

A caveat worth stating plainly rather than discovering later: against an
attacker who already has the secret, none of this holds. In production the
secret lives in Secret Manager and the database does not, so the two are not
compromised together. On a developer machine they usually are — there the MACs
are defence in depth, not a boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

#: Read at call time, never at import, so a secret bound after module load
#: (which is how Cloud Functions supplies it) is still picked up.
SECRET_ENV_VAR = "AUTOFILL_STAMP_SECRET"

#: Hex characters kept from the digest. 32 hex chars is 128 bits — far past
#: forgeable, and short enough to sit in a 255-character core property
#: alongside the rest of the keyword line.
MAC_CHARS = 32


class StampSecretMissing(RuntimeError):
    """
    Raised when the signing secret is not configured.

    Callers must let this propagate. Catching it and exporting anyway
    reintroduces both bypasses.
    """


def secret_available() -> bool:
    """For diagnostics and readiness checks — never to decide whether to sign."""
    return bool((os.environ.get(SECRET_ENV_VAR) or "").strip())


def _secret() -> bytes:
    raw = (os.environ.get(SECRET_ENV_VAR) or "").strip()
    if not raw:
        raise StampSecretMissing(
            f"{SECRET_ENV_VAR} is not set, so review state cannot be signed or "
            "verified. Refusing to produce an export whose provenance cannot be "
            "checked. Set it in Secret Manager for the deployed function, or in "
            ".env.local for local development — never in .env."
        )
    return raw.encode("utf-8")


def _canonical(payload: dict) -> bytes:
    """
    Stable bytes for a dict.

    `sort_keys` and a fixed separator mean the same logical payload always
    produces the same digest regardless of insertion order, and
    `ensure_ascii` keeps company names with non-ASCII characters from
    depending on the local encoding.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    ).encode("utf-8")


def sign(payload: dict) -> str:
    return hmac.new(_secret(), _canonical(payload), hashlib.sha256).hexdigest()[:MAC_CHARS]


def matches(candidate: str | None, payload: dict) -> bool:
    """
    Constant-time comparison of a supplied MAC against the one this payload
    should have. A missing or malformed MAC is a mismatch, not a pass.
    """
    if not candidate:
        return False
    return hmac.compare_digest(str(candidate), sign(payload))


# --- the two payload shapes -------------------------------------------------


def ack_payload(review_id: str, item_key: str, acknowledged_at: str,
                note: str) -> dict:
    """
    One acknowledgement. `acknowledged_at` and the note are both covered, so
    neither backdating an acknowledgement nor rewriting what the person said
    they checked survives verification.
    """
    return {
        "v": 1,
        "kind": "ack",
        "review_id": review_id or "",
        "item_key": item_key or "",
        "acknowledged_at": acknowledged_at or "",
        "note": note or "",
    }


def ack_mac(review_id: str, item_key: str, acknowledged_at: str, note: str) -> str:
    return sign(ack_payload(review_id, item_key, acknowledged_at, note))


def stamp_payload(company_id: str, review_id: str, source_sha256: str,
                  status: str, flags_total: int, flags_open: int,
                  filled_count: int, acknowledged_at_values, stamped_at: str) -> dict:
    """
    One document stamp, bound to the review record it came from.

    `acknowledged_at_values` is every acknowledgement timestamp in the review,
    sorted. Including them is what makes a forged database row visible: change
    one and this payload changes, so the MAC written into an earlier export no
    longer verifies against the current record.
    """
    return {
        "v": 1,
        "kind": "stamp",
        "company_id": company_id or "",
        "review_id": review_id or "",
        "source_sha256": source_sha256 or "",
        "status": status or "",
        "flags_total": int(flags_total),
        "flags_open": int(flags_open),
        "filled_count": int(filled_count),
        "acknowledged_at": sorted(str(v) for v in (acknowledged_at_values or [])),
        "stamped_at": stamped_at or "",
    }


def export_payload(company_id: str, review_id: str, final_sha256: str) -> dict:
    """
    Binds an export to the bytes of the finished file.

    The stamp MAC cannot do this on its own. It is written *into* the document,
    so it can only ever cover the digest of the draft as it stood beforehand —
    which is unrecoverable once stamping has rewritten the file. That left the
    stamp portable: the body of a genuine export could be rewritten, or the
    whole stamp lifted onto an unrelated document, and verification still
    passed because it only ever compared the stamp against the record.

    So the digest of the *finished* file is taken after stamping and signed
    separately, alongside the record. Editing the document changes the digest;
    editing the stored digest to match invalidates this MAC. Both are needed to
    forge one, and the secret produces neither.
    """
    return {
        "v": 1,
        "kind": "export",
        "company_id": company_id or "",
        "review_id": review_id or "",
        "final_sha256": final_sha256 or "",
    }


def file_sha256(path: str | Path) -> str:
    """
    Digest of the file as it stands before stamping.

    Stamping rewrites the .docx, so a digest of the finished file cannot be
    embedded in itself. This covers the draft the export was made from, which
    is the thing worth binding: swapping in different content produces a
    different digest from the one the review recorded.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
