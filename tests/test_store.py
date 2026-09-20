"""Store deletion: a whole run's files, a whole batch's data — idempotent, and gone for good."""

from __future__ import annotations

from pathlib import Path

from repoman.store import LocalStore


def test_delete_removes_one_key_and_is_idempotent(tmp_path):
    s = LocalStore(tmp_path)
    s.put_json("runs/x/a.json", {"k": 1})
    s.delete("runs/x/a.json")
    assert s.get_json("runs/x/a.json") is None
    s.delete("runs/x/a.json")  # already gone: not an error


def test_delete_prefix_removes_every_file_under_it(tmp_path):
    s = LocalStore(tmp_path)
    s.put_json("runs/y/a.json", {"k": 1})
    s.put_json("runs/y/b.json", {"k": 2})
    s.put_bytes("runs/y/repo/README.md", b"hello")  # the checkout directory
    s.delete_prefix("runs/y")
    assert s.list("runs/y") == []
    assert not (Path(tmp_path) / "runs" / "y").exists()


def test_delete_prefix_on_a_missing_prefix_is_not_an_error(tmp_path):
    s = LocalStore(tmp_path)
    s.delete_prefix("runs/never-existed")  # must not raise


def test_delete_prefix_leaves_sibling_data_alone(tmp_path):
    s = LocalStore(tmp_path)
    s.put_json("runs/keep/a.json", {"k": 1})
    s.put_json("runs/gone/a.json", {"k": 2})
    s.delete_prefix("runs/gone")
    assert s.get_json("runs/keep/a.json") == {"k": 1}
    assert s.get_json("runs/gone/a.json") is None


# --- S3Store: a fake boto3 client, enough to check the delete calls it makes -----------------


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.delete_objects_calls: list[list[str]] = []

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.encode()

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey({}, "GetObject")
        import io

        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise Exception("404")

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        matched = sorted(k for k in self.objects if k.startswith(Prefix))
        return {"Contents": [{"Key": k} for k in matched], "IsTruncated": False}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def delete_objects(self, Bucket, Delete):
        keys = [o["Key"] for o in Delete["Objects"]]
        self.delete_objects_calls.append(keys)
        for k in keys:
            self.objects.pop(k, None)

    class exceptions:
        class NoSuchKey(Exception):
            def __init__(self, *a):
                super().__init__()


def make_s3store(monkeypatch, prefix=""):
    from repoman.store.s3 import S3Store

    fake = FakeS3()
    monkeypatch.setattr("boto3.client", lambda *a, **k: fake)
    return S3Store("bucket", prefix=prefix), fake


def test_s3_delete_uses_the_prefixed_key(monkeypatch):
    store, fake = make_s3store(monkeypatch, prefix="myapp")
    store.put_json("batches/b1.json", {"id": "b1"})
    assert "myapp/batches/b1.json" in fake.objects
    store.delete("batches/b1.json")
    assert "myapp/batches/b1.json" not in fake.objects


def test_s3_delete_prefix_removes_everything_and_the_tarball(monkeypatch, tmp_path, monkeypatch2=None):
    import repoman.store.s3 as s3mod

    store, fake = make_s3store(monkeypatch, prefix="myapp")
    monkeypatch.setattr(s3mod, "CACHE_ROOT", tmp_path)  # so the "clear the local cache" step is inert but exercised
    store.put_json("runs/r1/submission.json", {"id": "r1"})
    store.put_json("runs/r1/findings.json", [])
    fake.objects["myapp/runs/r1.tar.gz"] = b"fake tar"

    store.delete_prefix("runs/r1")

    assert not any(k.startswith("myapp/runs/r1") for k in fake.objects)
    # S3 prefix matching is a raw string comparison, so listing "runs/r1" already catches the
    # sibling "runs/r1.tar.gz" in the same delete_objects batch — the explicit extra delete() call
    # after it is a no-op fallback for a trailing-slash prefix, not the thing that removes it here.
    assert fake.delete_objects_calls and set(fake.delete_objects_calls[0]) == {
        "myapp/runs/r1/submission.json", "myapp/runs/r1/findings.json", "myapp/runs/r1.tar.gz"}


def test_s3_delete_prefix_batches_at_1000_keys(monkeypatch):
    store, fake = make_s3store(monkeypatch)
    for i in range(1500):
        fake.objects[f"runs/big/{i}.json"] = b"{}"
    store.delete_prefix("runs/big")
    assert len(fake.delete_objects_calls) == 2  # 1000 + 500
    assert not any(k.startswith("runs/big") for k in fake.objects)
