"""`S3Store` — the same port, backed by one bucket. Track 2.

The only wrinkle is the checkout. `LocalStore` keeps it as a real directory the evidence pane
reads from; S3 has no directories, so a run's repository is tarred into the run prefix after
acquisition and extracted to a local cache the first time something needs it. That is what
`local_dir()` is for, and why `_evidence.html` already has a "checkout is not available on this
host" branch.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import threading
from pathlib import Path
from typing import Any

CACHE_ROOT = Path(os.environ.get("REPOMAN_CACHE_DIR", Path(os.environ.get("TMPDIR", "/tmp")) / "repoman"))


class S3Store:
    def __init__(self, bucket: str, prefix: str = ""):
        import boto3

        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.s3 = boto3.client("s3")
        self._lock = threading.Lock()  # extraction is not re-entrant; two requests can race

    def _key(self, key: str) -> str:
        key = key.lstrip("/")
        if ".." in Path(key).parts:
            raise ValueError(f"key escapes store root: {key}")
        return self.prefix + key

    # --- JSON and bytes ----------------------------------------------------------

    def put_json(self, key: str, value: Any) -> None:
        if hasattr(value, "model_dump"):
            value = value.model_dump(by_alias=True)
        elif isinstance(value, list) and value and hasattr(value[0], "model_dump"):
            value = [v.model_dump(by_alias=True) for v in value]
        body = json.dumps(value, indent=2, sort_keys=True).encode("utf-8")
        self.s3.put_object(Bucket=self.bucket, Key=self._key(key), Body=body,
                           ContentType="application/json")

    def get_json(self, key: str, default: Any = None) -> Any:
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=self._key(key))
        except self.s3.exceptions.NoSuchKey:
            return default
        except Exception as e:  # botocore raises ClientError for a 404 on some paths
            if getattr(e, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return default
            raise
        return json.loads(obj["Body"].read().decode("utf-8"))

    def put_bytes(self, key: str, data: bytes) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=self._key(key), Body=data)

    def get_bytes(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except Exception:
            return False

    def list(self, prefix: str) -> list[str]:
        out: list[str] = []
        token = None
        base = self._key(prefix)
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": base}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.s3.list_objects_v2(**kwargs)
            for item in page.get("Contents", []):
                key = item["Key"]
                out.append(key[len(self.prefix):] if self.prefix else key)
            if not page.get("IsTruncated"):
                return sorted(out)
            token = page.get("NextContinuationToken")

    # --- the checkout ------------------------------------------------------------

    def local_dir(self, key: str) -> Path:
        """A real directory for `key`, materialised from the run's tarball on first use."""
        local = (CACHE_ROOT / self._key(key)).resolve()
        if local.is_dir() and any(local.iterdir()):
            return local
        local.mkdir(parents=True, exist_ok=True)

        tar_key = key.rstrip("/") + ".tar.gz"
        with self._lock:
            if any(local.iterdir()):
                return local
            try:
                blob = self.get_bytes(tar_key)
            except Exception:
                return local  # nothing archived yet: the caller is about to write into it
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
                tar.extractall(local, members=_safe_members(tar, local), filter="data")
        return local

    def archive_dir(self, key: str, source: Path) -> None:
        """Tar a materialised directory back into the run prefix. Called after acquisition."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(source, arcname=".")
        self.put_bytes(key.rstrip("/") + ".tar.gz", buf.getvalue())


def _safe_members(tar: tarfile.TarFile, dest: Path):
    """A tarball we wrote is still untrusted on the way back in: it contains submitted files."""
    root = dest.resolve()
    for member in tar.getmembers():
        target = (root / member.name).resolve()
        if root == target or root in target.parents:
            if not (member.issym() or member.islnk()):
                yield member
