"""
Generated documents surviving the instance that made them.

`/tmp` is per-instance on Cloud Run. A filled SBD 1 produced in production
downloaded fine seconds after it was made and returned "File not found or
expired" ten minutes later — its ownership row still in Cloud SQL, the PDF
behind it gone, because a different instance answered the second request.

The bucket is stubbed here. What is under test is this module's decisions:
when it does nothing, when it fails soft, and — the one that matters for
security — that a restore can never be triggered by someone who does not own
the file.
"""

from __future__ import annotations

import json

import pytest

from agent import object_store


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(object_store.BUCKET_ENV, raising=False)
    monkeypatch.delenv("FIREBASE_CONFIG", raising=False)
    # A client cached from another test must not leak into this one.
    monkeypatch.setattr(object_store, "_client", None)
    monkeypatch.setattr(object_store, "_client_pid", None)


class _FakeBlob:
    def __init__(self, store, name):
        self._store, self.name = store, name

    def upload_from_filename(self, path):
        self._store[self.name] = open(path, "rb").read()

    def exists(self):
        return self.name in self._store

    def download_to_filename(self, path):
        with open(path, "wb") as fh:
            fh.write(self._store[self.name])

    def delete(self):
        self._store.pop(self.name, None)


class _FakeBucket:
    def __init__(self, store):
        self._store = store

    def blob(self, name):
        return _FakeBlob(self._store, name)


@pytest.fixture()
def bucket(monkeypatch):
    """A configured bucket whose contents the test can inspect."""
    store: dict[str, bytes] = {}
    monkeypatch.setenv(object_store.BUCKET_ENV, "test-bucket")
    monkeypatch.setattr(object_store, "enabled", lambda: True)
    monkeypatch.setattr(object_store, "_bucket", lambda: _FakeBucket(store))
    return store


# --- when it is switched on at all -----------------------------------------


def test_no_bucket_means_disabled():
    """Local development has no bucket, and that is a normal state."""
    assert object_store.bucket_name() is None
    assert object_store.enabled() is False


def test_the_bucket_comes_from_the_firebase_config_the_runtime_injects(monkeypatch):
    """
    So a correctly deployed service needs no extra setting.

    A second setting is a second thing that can drift from the first.
    """
    monkeypatch.setenv("FIREBASE_CONFIG",
                       json.dumps({"projectId": "cairoai",
                                   "storageBucket": "cairoai.firebasestorage.app"}))
    assert object_store.bucket_name() == "cairoai.firebasestorage.app"


def test_an_explicit_bucket_wins(monkeypatch):
    monkeypatch.setenv("FIREBASE_CONFIG",
                       json.dumps({"storageBucket": "from-config"}))
    monkeypatch.setenv(object_store.BUCKET_ENV, "explicit")
    assert object_store.bucket_name() == "explicit"


def test_unreadable_firebase_config_is_not_a_crash(monkeypatch):
    monkeypatch.setenv("FIREBASE_CONFIG", "{not json")
    assert object_store.bucket_name() is None


def test_disabled_upload_is_a_no_op(tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    assert object_store.upload(f) is False


# --- round trip ------------------------------------------------------------


def test_a_file_survives_the_instance_that_made_it(tmp_path, bucket):
    """The whole point: made on one instance, fetched from another."""
    made = tmp_path / "autofill_draft.pdf"
    made.write_bytes(b"%PDF-1.4 filled SBD 1")
    assert object_store.upload(made) is True

    made.unlink()                      # the instance is gone
    assert not made.exists()

    assert object_store.ensure_local("autofill_draft.pdf", made) is True
    assert made.read_bytes() == b"%PDF-1.4 filled SBD 1"


def test_ensure_local_does_nothing_when_the_file_is_already_there(tmp_path, bucket):
    """The common case must not cost a network round trip."""
    here = tmp_path / "present.pdf"
    here.write_bytes(b"original")
    # Nothing was ever uploaded, so a restore would fail — this passing proves
    # no fetch was attempted.
    assert object_store.ensure_local("present.pdf", here) is True
    assert here.read_bytes() == b"original"


def test_a_file_that_is_in_neither_place_is_reported_missing(tmp_path, bucket):
    gone = tmp_path / "never_existed.pdf"
    assert object_store.ensure_local("never_existed.pdf", gone) is False
    assert not gone.exists()


def test_objects_are_namespaced_under_a_prefix(tmp_path, bucket):
    """Generated documents must not collide with anything else in the bucket."""
    f = tmp_path / "report.pdf"
    f.write_bytes(b"x")
    object_store.upload(f)
    assert list(bucket) == ["generated/report.pdf"]


def test_only_the_basename_is_used(tmp_path, bucket):
    """A path segment in the name must not become a path in the bucket."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    object_store.upload(f, "../../etc/doc.pdf")
    assert list(bucket) == ["generated/doc.pdf"]


# --- failing soft ----------------------------------------------------------


def test_uploading_a_file_that_is_not_there_returns_false(tmp_path, bucket):
    assert object_store.upload(tmp_path / "absent.pdf") is False


def test_a_storage_error_never_raises(tmp_path, monkeypatch):
    """
    A generator that has spent a Vision call and a model call producing a
    document must not lose it to a storage failure. The file is still on this
    instance either way, which is where it would have been regardless.
    """
    class Exploding:
        def blob(self, name):
            raise RuntimeError("bucket on fire")

    monkeypatch.setattr(object_store, "enabled", lambda: True)
    monkeypatch.setattr(object_store, "_bucket", lambda: Exploding())

    f = tmp_path / "x.pdf"
    f.write_bytes(b"x")
    assert object_store.upload(f) is False              # not an exception
    assert object_store.ensure_local("x.pdf", tmp_path / "y.pdf") is False
    assert object_store.delete("x.pdf") is False


def test_mirroring_failure_does_not_break_registration(tmp_path, monkeypatch):
    """
    Registration owns whether a file is servable. If mirroring could take it
    down, a storage outage would mean generated files with no owner recorded —
    unservable forever, rather than unservable until the bucket recovers.
    """
    from agent import generated_files

    def boom(*a, **k):
        raise RuntimeError("storage down")

    monkeypatch.setattr(object_store, "enabled", lambda: True)
    monkeypatch.setattr(object_store, "upload", boom)
    generated_files._mirror("something.pdf")            # must not raise
