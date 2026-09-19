"""Re-read what a model claimed. Invariant 3: locators are verified, not trusted.

`docs/04-model-orchestration.md` Rule 2 is the specification. Nothing here takes a draft
at its word: the path is checked for escape, the quote is matched against the bytes on
disk at the pinned commit, and the quote that gets stored is the text that was actually
there — never the model's rendering of it.

A draft that does not resolve is dropped and counted. It is never rendered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal, Sequence

from repoman.core.types import DocSpan, Evidence, FileRange, LocatorDraft

RESCUE_LINES = 10  # docs/04 Rule 2 step 4: models are right about the code and off about the line

_WS = re.compile(r"\s+")

# Characters with no visible width. A model cannot see them, so it cannot reproduce them, and a
# real citation must not die because the file carried a BOM, an emoji joiner or Indic formatting.
# Folding them affects *matching only*: `reread()` still returns the file's own bytes, so the
# evaluator sees the true text, and the injection probe flags hidden characters independently.
_INVISIBLE = dict.fromkeys(
    ord(c) for c in "​‌‍‎‏‪‫‬‭‮"
                    "⁠⁡⁢⁣﻿")


@dataclass(frozen=True)
class Checkout:
    """Everything a locator can be resolved against. Built once per run, read-only thereafter."""

    root: Path
    commitSha: str
    pages: dict[str, str] = field(default_factory=dict)  # report_pages.json: {"1": "text", ...}
    captures: tuple[Evidence, ...] = ()  # probe-made http_capture / git_object evidence


# --- whitespace-insensitive matching that can still point back at the original bytes ---


def normalise(text: str) -> str:
    """Collapse whitespace and drop zero-width characters. Neither difference is a real mismatch.

    Unicode spaces, NBSP included, are already whitespace to `\\s`, so they fold here too.
    """
    return _WS.sub(" ", text.translate(_INVISIBLE)).strip()


def _normalise_with_offsets(text: str) -> tuple[str, list[int]]:
    """`normalise(text)` plus, for each character in it, its offset in the original."""
    out: list[str] = []
    offsets: list[int] = []
    prev_space = True  # True at the start so leading whitespace is dropped, as strip() would
    for i, ch in enumerate(text):
        if ord(ch) in _INVISIBLE:
            continue  # must fold exactly what normalise() folds, or the offsets desync from it
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                offsets.append(i)
                prev_space = True
        else:
            out.append(ch)
            offsets.append(i)
            prev_space = False
    while out and out[-1] == " ":
        out.pop()
        offsets.pop()
    return "".join(out), offsets


def reread(haystack: str, quote: str) -> str | None:
    """The quote as it *actually appears* in `haystack`, or None if it is not there.

    Matching ignores whitespace differences; the returned string is sliced out of `haystack`,
    so what we store is the source's own text. That is what makes `Evidence.quote` a fact
    rather than an echo — and it is why the UI can highlight it by exact substring.
    """
    target = normalise(quote)
    if not target:
        return None  # an empty quote would match anything; it is not evidence
    norm, offsets = _normalise_with_offsets(haystack)
    at = norm.find(target)
    if at < 0:
        return None
    return haystack[offsets[at]: offsets[at + len(target) - 1] + 1]


# --- file_range ----------------------------------------------------------------


def _read_lines(root: Path, rel: str) -> list[str] | None:
    """Lines of `rel` inside the checkout, or None if it is missing or climbs out of it."""
    root = root.resolve()
    try:
        p = (root / rel).resolve()
    except (OSError, ValueError):
        return None
    if p != root and root not in p.parents:
        return None  # a path that escapes the checkout is an attack, not a typo
    try:
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None


def _span_containing(lines: Sequence[str], lo: int, hi: int, quote: str) -> tuple[int, int] | None:
    """Tightest 1-indexed line span within [lo, hi] whose text contains the quote."""
    target = normalise(quote)
    if not target or target not in normalise("\n".join(lines[lo - 1:hi])):
        return None  # one cheap check up front: the common case is a miss, and this ends it
    start = lo
    for end in range(lo, hi + 1):
        if target in normalise("\n".join(lines[start - 1:end])):
            while start < end and target in normalise("\n".join(lines[start:end])):
                start += 1
            return start, end
    return None  # unreachable: the window contains it, so some prefix does


def _resolve_file_range(loc: FileRange, quote: str, ck: Checkout) -> tuple[FileRange, str] | None:
    lines = _read_lines(ck.root, loc.path)
    if not lines:
        return None
    n = len(lines)
    lo = max(1, min(loc.startLine, n))
    hi = max(lo, min(loc.endLine, n))

    span = _span_containing(lines, lo, hi, quote)
    if span is not None:
        # The quote is where the model said it was. Keep the model's range — it marks the
        # region worth reading — and store the exact text found inside it.
        text = reread("\n".join(lines[lo - 1:hi]), quote)
        return FileRange(path=loc.path, startLine=lo, endLine=hi, commitSha=ck.commitSha), text or ""

    lo_w, hi_w = max(1, lo - RESCUE_LINES), min(n, hi + RESCUE_LINES)
    span = _span_containing(lines, lo_w, hi_w, quote)
    if span is None:
        return None
    # Off by a few lines. The cited range was wrong, so it is replaced rather than kept.
    start, end = span
    text = reread("\n".join(lines[start - 1:end]), quote)
    return FileRange(path=loc.path, startLine=start, endLine=end, commitSha=ck.commitSha), text or ""


# --- doc_span ------------------------------------------------------------------


def _resolve_doc_span(loc: DocSpan, quote: str, ck: Checkout) -> tuple[DocSpan, str] | None:
    """No rescue across pages: a doc_span whose quote is not on the cited page is dropped (AGENTS.md)."""
    page = ck.pages.get(str(loc.page))
    if page is None:
        return None
    text = reread(page, quote)
    return (loc, text) if text is not None else None


# --- http_capture / git_object -------------------------------------------------


def _resolve_capture(loc, quote: str, ck: Checkout) -> tuple[object, str] | None:
    """A capture the model cites must be one a probe actually made.

    The model never fetches anything (docs/05 boundary 2), so the only honest capture is one
    already recorded in `probes.json`. The locator must match a recorded one exactly; the quote
    is taken from that record, not from the model.
    """
    for ev in ck.captures:
        other = ev.locator
        if other.kind != loc.kind:
            continue
        if loc.kind == "http_capture" and (other.url != loc.url or other.status != loc.status):
            continue
        if loc.kind == "git_object" and other.commitSha != loc.commitSha:
            continue
        return other, (reread(ev.quote, quote) or ev.quote)
    return None


# --- the entry points ----------------------------------------------------------


def resolve(draft: LocatorDraft, ck: Checkout, *, submission_id: str,
            provenance: Literal["probe", "model"] = "model", probe_id: str | None = None) -> Evidence | None:
    """A drafted citation becomes Evidence only if it is found where it says it is."""
    loc = draft.locator
    if loc.kind == "file_range":
        found = _resolve_file_range(loc, draft.quote, ck)
    elif loc.kind == "doc_span":
        found = _resolve_doc_span(loc, draft.quote, ck)
    else:
        found = _resolve_capture(loc, draft.quote, ck)
    if found is None:
        return None
    locator, quote = found
    return Evidence(submissionId=submission_id, locator=locator, quote=quote,
                    provenance=provenance, probeId=probe_id)


def resolve_all(drafts: Iterable[LocatorDraft], ck: Checkout, *, submission_id: str,
                provenance: Literal["probe", "model"] = "model",
                probe_id: str | None = None) -> tuple[list[Evidence], list[LocatorDraft]]:
    """(kept, dropped). The dropped list is what the finding reports as "cited but did not resolve"."""
    kept: list[Evidence] = []
    dropped: list[LocatorDraft] = []
    for d in drafts:
        ev = resolve(d, ck, submission_id=submission_id, provenance=provenance, probe_id=probe_id)
        (kept if ev is not None else dropped).append(ev if ev is not None else d)
    return kept, dropped
