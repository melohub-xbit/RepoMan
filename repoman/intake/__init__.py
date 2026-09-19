"""Stage 01–02: a URL or a zip becomes a checkout on disk, pinned at a commit.

Everything privileged happens here, decided by deterministic code from what the evaluator
typed — never by a model (docs/05 boundary 2). By the time `verify/` runs, there is nothing
left to acquire: the agent can only read what this module already fetched.

This module never runs anything inside the checkout. Not a build, not a test, not an install.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from repoman.core.types import Artifact, Submission, new_id

CLONE_TIMEOUT = 180  # seconds; a submission that cannot be cloned in three minutes is a failed run
CLONE_DEPTH = 200  # enough history for the git probe's timeline without paying for a full mirror
MAX_UNPACKED = 512 * 1024 * 1024  # a zip that expands past this is a bomb, not a submission
README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md")


class IntakeError(RuntimeError):
    """Acquisition failed. The caller turns this into a visible failed run, never a missing one."""


@dataclass
class Acquired:
    submission: Submission
    pages: dict[str, str] = field(default_factory=dict)  # report_pages.json, {"1": "text", ...}


# --- git ------------------------------------------------------------------------


def _git(*args: str, cwd: Path | None = None, timeout: int = 30) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GCM_INTERACTIVE": "never"}
    try:
        out = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
                             timeout=timeout, check=True)
    except subprocess.TimeoutExpired as e:
        raise IntakeError(f"git {args[0]} timed out after {timeout}s") from e
    except subprocess.CalledProcessError as e:
        raise IntakeError(f"git {args[0]} failed: {(e.stderr or '').strip().splitlines()[-1:] or ['unknown']}"[:300]) from e
    except FileNotFoundError as e:
        raise IntakeError("git is not installed on this host") from e
    return out.stdout


def clone(repo_url: str, dest: Path) -> str:
    """Shallow clone into `dest`, returning the commit SHA every locator will be pinned to.

    `GIT_TERMINAL_PROMPT=0` matters: without it a private or mistyped URL hangs on a credential
    prompt that nobody is there to answer.
    """
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git("clone", "--depth", str(CLONE_DEPTH), "--no-tags", "--single-branch", repo_url, str(dest),
         timeout=CLONE_TIMEOUT)
    return _git("rev-parse", "HEAD", cwd=dest).strip()


# --- zip ------------------------------------------------------------------------


def _safe_members(zf: zipfile.ZipFile, dest: Path) -> list[zipfile.ZipInfo]:
    """Entries that stay inside `dest`. A `../` in a submitted zip is an attack on the grader."""
    root = dest.resolve()
    total = 0
    out = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts or (len(name) > 1 and name[1] == ":"):
            continue
        target = (root / name).resolve()
        if root not in target.parents:
            continue
        total += info.file_size
        if total > MAX_UNPACKED:
            raise IntakeError(f"zip expands past {MAX_UNPACKED // (1024 * 1024)}MB; refusing to unpack it")
        out.append(info)
    return out


def unzip(zip_path: str | Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = _safe_members(zf, dest)
            for info in members:
                target = dest / info.filename.replace("\\", "/")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, length=1 << 20)
    except zipfile.BadZipFile as e:
        raise IntakeError("not a readable zip archive") from e
    # A zip of a repo is usually one wrapper directory deep; unwrap it so paths match the repo's own.
    entries = [p for p in dest.iterdir() if p.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for child in list(inner.iterdir()):
            shutil.move(str(child), str(dest / child.name))
        inner.rmdir()


# --- report PDF -----------------------------------------------------------------


def read_report(report_path: str | Path) -> dict[str, str]:
    """PDF → {"1": text, ...}. docs/04 Rule 3: pages come from pypdf, never from the model.

    This dict is the *only* form the report exists in downstream, so `read_report_page` and the
    resolver are guaranteed to be looking at identical text.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(report_path))
        return {str(i): (page.extract_text() or "") for i, page in enumerate(reader.pages, start=1)}
    except Exception as e:  # a corrupt or encrypted PDF is a missing artifact, not a crashed run
        raise IntakeError(f"could not read the report PDF: {type(e).__name__}") from e


# --- artifacts ------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_readme(repo: Path) -> Path | None:
    for name in README_NAMES:
        p = repo / name
        if p.is_file():
            return p
    return next((p for p in sorted(repo.glob("*")) if p.is_file() and p.name.lower().startswith("readme")), None)


# --- the entry point -------------------------------------------------------------


def acquire(dest: Path, *, batch_id: str, submission_id: str | None = None, repo_url: str | None = None,
            zip_path: str | None = None, report_path: str | None = None, deploy_url: str | None = None) -> Acquired:
    """Fetch everything a run will ever read, and record what was fetched.

    `dest` is the run's directory in the Store. The checkout lands at `dest/repo`.
    """
    if not repo_url and not zip_path:
        raise IntakeError("a submission needs a repository URL or a zip")

    dest.mkdir(parents=True, exist_ok=True)
    repo = dest / "repo"
    sub_id = submission_id or new_id()

    if repo_url:
        commit_sha = clone(repo_url, repo)
        source = "github"
    else:
        unzip(zip_path, repo)
        # A zip has no commit of its own. Hash the tree so locators still pin to *something*
        # content-addressed, and permalinks degrade to plain path:line in the UI.
        commit_sha = _tree_hash(repo)
        source = "zip"

    artifacts = [Artifact(submissionId=sub_id, kind="repo", uri=repo_url or str(zip_path))]

    readme = find_readme(repo)
    if readme:
        artifacts.append(Artifact(submissionId=sub_id, kind="readme",
                                  uri=str(readme.relative_to(repo)).replace("\\", "/"), sha256=_sha256(readme)))

    pages: dict[str, str] = {}
    if report_path and Path(report_path).is_file():
        pages = read_report(report_path)
        artifacts.append(Artifact(submissionId=sub_id, kind="report", uri="report.pdf",
                                  sha256=_sha256(Path(report_path))))

    if deploy_url:
        # Recorded, not fetched. The GET is the deploy probe's job, at stage 04.
        artifacts.append(Artifact(submissionId=sub_id, kind="deploy", uri=deploy_url))

    sub = Submission(id=sub_id, batchId=batch_id, source=source, repoUrl=repo_url,
                     commitSha=commit_sha, artifacts=artifacts)
    return Acquired(submission=sub, pages=pages)


def _tree_hash(root: Path) -> str:
    """A stable identity for a zip submission: sha256 over (relative path, content hash), sorted."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).replace("\\", "/").encode())
            h.update(_sha256(p).encode())
    return h.hexdigest()
