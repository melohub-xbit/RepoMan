"""Cross-submission similarity: "these four share an identical service layer."

Plagiarism and collusion detection that no existing tool gives a professor (`docs/01` §2), and
the only check here that cannot be answered by looking at one submission. It is structurally
different from every other probe:

- A submission's **fingerprint** is computed during its run: a normalised hash per source file.
- The **comparison** happens across a batch, and its answer changes every time a new submission
  arrives — the fifth project can reveal that the first two were copies of each other.

That second point decides the design. Findings are an audit record of what was true when the run
happened, so a later submission must never rewrite an earlier one's findings. The comparison is
therefore computed on demand from stored fingerprints and shown as a batch-level fact, not
retro-fitted into findings that were already shown to a human.

Normalisation is deliberately shallow — whitespace and comments only. Detecting a renamed-variable
copy is a different and much harder problem, and claiming to do it badly would be worse than not
claiming it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from repoman.probes import read_text, rel, walk

MIN_LINES = 4  # a three-line file is boilerplate; matching one proves nothing
SHARED_THRESHOLD = 0.30  # share of the smaller submission's files that must match to be worth a look
MIN_SHARED_FILES = 3

CODE_SUFFIXES = {".java", ".kt", ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".rs", ".php",
                 ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".scala", ".vue", ".svelte"}

# Files every project of a given stack contains identically. Matching these is not evidence.
BOILERPLATE = {"__init__.py", "setup.py", "manage.py", "index.js", "index.ts", "main.ts",
               "App.js", "App.tsx", "vite-env.d.ts", "next-env.d.ts"}

_COMMENT = re.compile(r"//.*?$|/\*.*?\*/|^\s*#.*?$", re.S | re.M)
_WS = re.compile(r"\s+")


def normalise_code(text: str) -> str:
    """Strip comments and collapse whitespace. Reformatting is not originality; renaming is not caught."""
    return _WS.sub(" ", _COMMENT.sub(" ", text)).strip()


def fingerprint(repo: Path) -> dict[str, str]:
    """{relative path: content hash} for the files worth comparing."""
    out: dict[str, str] = {}
    for p in walk(repo, CODE_SUFFIXES):
        if p.name in BOILERPLATE:
            continue
        text = read_text(p)
        if not text or len(text.splitlines()) < MIN_LINES:
            continue
        body = normalise_code(text)
        if len(body) < 80:
            continue
        out[rel(repo, p)] = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    return out


@dataclass
class Overlap:
    a: str
    b: str
    shared: list[str]
    share: float  # of the smaller submission

    def as_json(self) -> dict:
        return {"a": self.a, "b": self.b, "shared": self.shared[:20],
                "sharedCount": len(self.shared), "share": round(self.share, 3)}


def compare(fingerprints: dict[str, dict[str, str]]) -> list[Overlap]:
    """Every pair of submissions that shares enough identical files to be worth a human's attention.

    Matching is by content hash, so a shared file counts even when the two projects filed it under
    different names — which is what a reskin looks like.
    """
    runs = sorted(fingerprints)
    out: list[Overlap] = []
    for i, a in enumerate(runs):
        for b in runs[i + 1:]:
            fa, fb = fingerprints[a], fingerprints[b]
            if not fa or not fb:
                continue
            hashes_b = {}
            for path, h in fb.items():
                hashes_b.setdefault(h, path)
            shared = sorted({path for path, h in fa.items() if h in hashes_b})
            if not shared:
                continue
            share = len(shared) / min(len(fa), len(fb))
            if len(shared) >= MIN_SHARED_FILES and share >= SHARED_THRESHOLD:
                out.append(Overlap(a=a, b=b, shared=shared, share=share))
    out.sort(key=lambda o: (-o.share, -len(o.shared)))
    return out


def summarise(overlap: Overlap, names: dict[str, str] | None = None) -> str:
    names = names or {}
    a = names.get(overlap.a, overlap.a)
    b = names.get(overlap.b, overlap.b)
    n = len(overlap.shared)
    return (f"{a} and {b} share {n} identical file{'' if n == 1 else 's'} "
            f"({round(overlap.share * 100)}% of the smaller submission), including "
            f"{', '.join(overlap.shared[:3])}.")
