"""Timeline and contribution shape, read from the git history.

This probe answers questions an evaluator asks out loud: when was this actually written, by how
many people, and did it all arrive an hour before the deadline. None of that is a verdict — a
team that commits in one push may have pair-programmed all week — so the probe reports the shape
and flags it for a human, never scores it.

Author emails never survive this function: they are hashed on the way in (docs/05 boundary 4).
"""

from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from repoman.core.types import Evidence, GitObject, Submission, hash_author
from repoman.probes import ProbeResult

CRUNCH_HOURS = 6  # the window before a deadline that "the night before" means
SKEW_SHARE = 0.80  # one author writing this much of the lines is worth a human's attention
DUMP_SHARE = 0.70  # one commit carrying this much of the code is not incremental work
UNIT = "\x1f"
RECORD = "\x1e"


def _git(repo: Path, *args: str) -> str:
    # Without this, `git log` in a directory that is not itself a repository walks *up* and
    # reports the enclosing repository's history — so a zip extracted inside our own tree would
    # be graded on our commits. The checkout must be its own root.
    if not (repo / ".git").exists():
        return ""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}
    try:
        out = subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True,
                             timeout=60, check=True)
        return out.stdout
    except (subprocess.SubprocessError, OSError):
        return ""  # a zip submission has no history; that is a fact, not a failure


def _parse_iso(value: str) -> datetime | None:
    """Always UTC-aware. Git stamps carry an offset; a date typed into the batch form does not."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _window_bounds(event_window: tuple[str, str] | None) -> tuple[datetime | None, datetime | None]:
    if not event_window:
        return None, None
    start, end = (_parse_iso(v) for v in event_window)
    if end is not None and end.hour == 0 and end.minute == 0:
        end = end + timedelta(days=1)  # a date-only deadline means the end of that day
    return start, end


def probe(repo: Path, sub: Submission, event_window: tuple[str, str] | None = None) -> ProbeResult:
    log = _git(repo, "log", f"--format={RECORD}%H{UNIT}%ae{UNIT}%aI{UNIT}%s", "--numstat")
    if not log.strip():
        return ProbeResult(summary="No git history available for this submission.",
                           data={"commits": 0, "authors": 0})

    commits = []
    for chunk in log.split(RECORD):
        if not chunk.strip():
            continue
        head, _, body = chunk.partition("\n")
        parts = head.split(UNIT)
        if len(parts) < 4:
            continue
        sha, email, when, subject = parts[0], parts[1], parts[2], UNIT.join(parts[3:])
        added = 0
        for line in body.splitlines():
            cols = line.split("\t")
            if len(cols) == 3 and cols[0].isdigit():
                added += int(cols[0])
        commits.append({"sha": sha, "author": hash_author(email), "at": when.strip(),
                        "subject": subject.strip(), "added": added})

    if not commits:
        return ProbeResult(summary="No git history available for this submission.",
                           data={"commits": 0, "authors": 0})

    by_author_lines: dict[str, int] = defaultdict(int)
    by_author_commits: dict[str, int] = defaultdict(int)
    for c in commits:
        by_author_lines[c["author"]] += c["added"]
        by_author_commits[c["author"]] += 1

    total_lines = sum(by_author_lines.values())
    top_author, top_lines = max(by_author_lines.items(), key=lambda kv: kv[1])
    top_share = (top_lines / total_lines) if total_lines else 0.0

    start, end = _window_bounds(event_window)
    times = [(_parse_iso(c["at"]), c) for c in commits]
    times = [(t, c) for t, c in times if t is not None]

    before_window = [c for t, c in times if start and t < start]
    crunch = []
    if end:
        crunch = [c for t, c in times if end - timedelta(hours=CRUNCH_HOURS) <= t <= end]
    crunch_share = len(crunch) / len(commits) if commits else 0.0

    dump = next((c for c in commits if total_lines and c["added"] / total_lines >= DUMP_SHARE), None)

    # What the fork probe needs to judge whether the project arrived whole. A shallow clone's
    # oldest commit only *looks* like a root, so say when the history was truncated rather than
    # letting a depth limit read as "this was copied".
    truncated = (repo / ".git" / "shallow").exists()
    tracked = [p for p in _git(repo, "ls-files").splitlines() if p.strip()]
    root_commit = commits[-1]
    root_files = [line for line in _git(repo, "show", "--numstat", "--format=", root_commit["sha"]).splitlines()
                  if line.strip()]
    root_share = (len(root_files) / len(tracked)) if tracked else None

    flags, notes = [], []
    if len(by_author_lines) > 1 and top_share >= SKEW_SHARE:
        flags.append("CONTRIBUTION_SKEW")
    if before_window:
        flags.append("TIMELINE_ANOMALY")
        n = len(before_window)
        notes.append(f"{n} commit{'' if n == 1 else 's'} predate{'s' if n == 1 else ''} the event window")
    if end and crunch_share > 0.5:
        if "TIMELINE_ANOMALY" not in flags:
            flags.append("TIMELINE_ANOMALY")
        notes.append(f"{round(crunch_share * 100)}% of commits in the {CRUNCH_HOURS} h before the deadline")
    if dump is not None and len(commits) > 1:
        if "TIMELINE_ANOMALY" not in flags:
            flags.append("TIMELINE_ANOMALY")
        notes.append(f"one commit carries {round(dump['added'] / total_lines * 100)}% of the added lines")

    def git_evidence(c: dict) -> Evidence:
        return Evidence(submissionId=sub.id, provenance="probe", probeId="git",
                        quote=c["subject"] or c["sha"][:10],
                        locator=GitObject(commitSha=c["sha"], authorHash=c["author"], committedAt=c["at"]))

    evidence = []
    if dump is not None and len(commits) > 1:
        evidence.append(git_evidence(dump))
    evidence += [git_evidence(c) for c in before_window[:3]]
    evidence += [git_evidence(c) for c in crunch[:3] if c is not dump]
    if not evidence:
        evidence.append(git_evidence(commits[0]))

    authors = len(by_author_lines)
    summary = (f"{len(commits)} commit{'' if len(commits) == 1 else 's'} · "
               f"{authors} author{'' if authors == 1 else 's'}")
    if authors > 1 and total_lines:
        summary += f" · {round(top_share * 100)}% of lines by one author"
    if notes:
        summary += " · " + " · ".join(notes)

    return ProbeResult(summary=summary + ".", evidence=evidence[:6], flags=flags, data={
        "commits": len(commits), "authors": authors, "totalLinesAdded": total_lines,
        "topAuthorShare": round(top_share, 3),
        "commitsByAuthor": dict(by_author_commits), "linesByAuthor": dict(by_author_lines),
        "commitsBeforeWindow": len(before_window), "crunchShare": round(crunch_share, 3),
        "singleDumpCommit": dump["sha"] if dump and len(commits) > 1 else None,
        "firstCommitAt": commits[-1]["at"], "lastCommitAt": commits[0]["at"],
        "historyTruncated": truncated, "files": len(tracked), "rootFileShare": root_share,
        "rootCommit": root_commit,
    })
