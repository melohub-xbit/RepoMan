"""Fork and reskin detection: did this project start as somebody else's?

`docs/01` §2 asks for three signals, and they are deliberately different in kind:

- **an orphaned initial commit** — the history begins with the project already written. Real work
  accumulates; a copy arrives whole.
- **a single large code dump** — the same shape appearing later in the history.
- **upstream comparison** — GitHub itself knows whether a repository is a fork, and of what.

None of these is an accusation. A team that developed on a private repo and pushed once looks
identical to a team that copied, and RepoMan cannot tell those apart — so it reports the shape and
hands it to a human, exactly like every other flag.

The upstream check takes a client because it needs the network (AGENTS.md), and it asks about a URL
the *evaluator* typed, never one found inside the submission (docs/05 boundary 2).
"""

from __future__ import annotations

import re
from pathlib import Path

from repoman.core.types import Evidence, GitObject, Submission
from repoman.probes import ProbeResult

ORPHAN_SHARE = 0.60  # the first commit carrying this much of the tree is a project that arrived whole
MIN_FILES = 5  # below this, "most of the repo in one commit" means nothing
TIMEOUT = 10.0

GITHUB_REPO = re.compile(r"github\.com[:/]+([^/]+)/([^/.]+)", re.I)


def _api_url(repo_url: str | None) -> str | None:
    m = GITHUB_REPO.search(repo_url or "")
    return f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}" if m else None


def upstream(sub: Submission, client=None) -> dict:
    """What GitHub says about the repository itself. `{}` when unknown — never a guess."""
    url = _api_url(sub.repoUrl)
    if not url:
        return {}
    owns = client is None
    if owns:
        import httpx

        client = httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                              headers={"User-Agent": "RepoMan/0.1 (evidence probe)",
                                       "Accept": "application/vnd.github+json"})
    try:
        r = client.get(url)
        if r.status_code != 200:
            return {}
        data = r.json()
        return {"fork": bool(data.get("fork")),
                "parent": (data.get("parent") or {}).get("full_name"),
                "createdAt": data.get("created_at"),
                "pushedAt": data.get("pushed_at")}
    except Exception:
        return {}  # the check not running is not evidence of anything
    finally:
        if owns:
            client.close()


def probe(repo: Path, sub: Submission, git_data: dict | None = None, client=None) -> ProbeResult:
    """`git_data` is the `git` probe's output, so the history is parsed once per run."""
    git_data = git_data or {}
    commits = git_data.get("commits", 0)
    if not commits:
        return ProbeResult(summary="No git history, so origin cannot be checked.",
                           data={"checked": False})

    signals: list[str] = []
    evidence: list[Evidence] = []
    flags = []

    root = git_data.get("rootCommit")
    truncated = git_data.get("historyTruncated")
    root_share = git_data.get("rootFileShare")

    if root and not truncated and root_share is not None and git_data.get("files", 0) >= MIN_FILES:
        if root_share >= ORPHAN_SHARE:
            signals.append(f"the first commit already contains {round(root_share * 100)}% of the files")
            flags.append("FORK_SUSPECTED")
            evidence.append(Evidence(
                submissionId=sub.id, provenance="probe", probeId="fork",
                quote=root.get("subject") or root.get("sha", "")[:10],
                locator=GitObject(commitSha=root["sha"], authorHash=root["author"],
                                  committedAt=root["at"])))
    elif truncated:
        signals.append(f"history is truncated at {commits} commits, so the first commit was not examined")

    if git_data.get("singleDumpCommit"):
        signals.append("one commit carries most of the code")
        if "FORK_SUSPECTED" not in flags:
            flags.append("FORK_SUSPECTED")

    up = upstream(sub, client=client)
    if up.get("fork"):
        parent = up.get("parent")
        signals.append(f"GitHub reports this repository is a fork" + (f" of {parent}" if parent else ""))
        if "FORK_SUSPECTED" not in flags:
            flags.append("FORK_SUSPECTED")

    if not signals:
        summary = "The history starts small and grows; GitHub does not report it as a fork."
        if not up:
            summary = "The history starts small and grows. Upstream could not be checked."
        return ProbeResult(summary=summary, data={"checked": True, "upstream": up})

    return ProbeResult(summary=("Origin: " + "; ".join(signals) + "."), evidence=evidence, flags=flags,
                       data={"checked": True, "upstream": up, "signals": signals,
                             "rootFileShare": root_share})
