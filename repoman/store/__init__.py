"""The one port. LocalStore for Track 1, S3Store for Track 2. Chosen from env at startup.

Keys are slash-separated paths, e.g. "runs/abc/findings.json". Layout is in docs/02.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol


class Store(Protocol):
    def put_json(self, key: str, value: Any) -> None: ...
    def get_json(self, key: str, default: Any = None) -> Any: ...
    def put_bytes(self, key: str, data: bytes) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def list(self, prefix: str) -> list[str]: ...
    def local_dir(self, key: str) -> Path:
        """A real directory for `key` (checkouts). LocalStore: in place. S3Store: /tmp cache."""
        ...

    def archive_dir(self, key: str, source: Path) -> None:
        """Persist a materialised directory. LocalStore: already persisted, so nothing to do.

        This is on the port rather than behind an `if is_cloud` in the pipeline: the checkout
        needs tarring into the run prefix on S3 and needs nothing on disk, and that difference
        is exactly what a port is for.
        """
        ...


class LocalStore:
    def __init__(self, root: str | Path = "data"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError(f"key escapes store root: {key}")
        return p

    def put_json(self, key: str, value: Any) -> None:
        if hasattr(value, "model_dump"):
            value = value.model_dump(by_alias=True)
        elif isinstance(value, list) and value and hasattr(value[0], "model_dump"):
            value = [v.model_dump(by_alias=True) for v in value]
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(p)  # atomic on POSIX; a reader never sees a half-written file

    def get_json(self, key: str, default: Any = None) -> Any:
        p = self._p(key)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    def put_bytes(self, key: str, data: bytes) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._p(key).exists()

    def list(self, prefix: str) -> list[str]:
        base = self._p(prefix)
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file() and not p.name.endswith(".tmp"))

    def local_dir(self, key: str) -> Path:
        p = self._p(key)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def archive_dir(self, key: str, source: Path) -> None:
        """Nothing to do: the checkout is already where it lives."""


def make_store() -> Store:
    bucket = os.environ.get("REPOMAN_BUCKET")
    if bucket:
        from repoman.store.s3 import S3Store  # Track A, Day 2

        return S3Store(bucket)
    return LocalStore(os.environ.get("REPOMAN_DATA", "data"))


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        s = LocalStore(d)
        s.put_json("runs/x/a.json", {"k": 1})
        assert s.get_json("runs/x/a.json") == {"k": 1}
        assert s.get_json("runs/x/missing.json", []) == []
        assert s.list("runs") == ["runs/x/a.json"]
        try:
            s.get_json("../etc/passwd")
            raise SystemExit("path escape allowed")
        except ValueError:
            pass
    print("store ok")
