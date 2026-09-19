"""Stage 04: deterministic checks. Pure functions from a checkout to located facts.

Two reasons this layer exists, and the second matters more:

1. If a check can be computed, compute it — cheaper, faster, and it cannot hallucinate.
2. A card badged `probe` is read differently by the human than one badged `model`. Probes are
   what stop a findings list from reading like a language model's opinion.

`probes` may not import `verify`. The dependency direction is a security control (docs/05).
No probe executes anything from the submission.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel

from repoman.core.types import Evidence, FileRange, FlagKind, Submission

PROBE_VERSION = "1"  # bumped when a probe's output changes meaning; recorded in the RunManifest

VENDORED = {"node_modules", "target", ".git", "dist", "build", "out", "bin", "obj", "vendor",
            "venv", ".venv", "env", "__pycache__", ".gradle", ".idea", ".mvn", "coverage",
            ".next", ".nuxt", ".pytest_cache", ".tox", "site-packages"}

SOURCE_SUFFIXES = {".java", ".kt", ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".rs",
                   ".php", ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".scala", ".m",
                   ".vue", ".svelte", ".yml", ".yaml", ".toml", ".gradle", ".xml", ".json",
                   ".md", ".txt", ".properties", ".sql", ".sh"}

MAX_FILE_BYTES = 1_000_000  # a source file larger than this is generated or vendored


class ProbeResult(BaseModel):
    """What one probe found. Serialised into `runs/<id>/probes.json` under the probe's name."""

    summary: str
    evidence: list[Evidence] = []
    flags: list[FlagKind] = []
    data: dict[str, Any] = {}


def walk(root: Path, suffixes: set[str] | None = None) -> Iterator[Path]:
    """Every non-vendored file under `root`, deepest-first-stable. Vendored trees are never read."""
    if not root.is_dir():
        return
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.is_dir():
                if p.name not in VENDORED and not p.name.startswith(".git"):
                    stack.append(p)
            elif suffixes is None or p.suffix.lower() in suffixes:
                yield p


def read_text(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def line_evidence(root: Path, path: Path, line_no: int, quote: str, sub: Submission, probe_id: str,
                  *, end_line: int | None = None) -> Evidence:
    """A probe's own citation. Probe evidence is produced from bytes this code just read, so it is
    already resolved — it does not go back through `core.resolver`."""
    return Evidence(
        submissionId=sub.id, provenance="probe", probeId=probe_id, quote=quote,
        locator=FileRange(path=rel(root, path), startLine=line_no, endLine=end_line or line_no,
                          commitSha=sub.commitSha),
    )


def find_lines(text: str, needle: str) -> list[int]:
    """1-indexed line numbers whose text contains `needle`."""
    return [i for i, line in enumerate(text.splitlines(), start=1) if needle in line]
