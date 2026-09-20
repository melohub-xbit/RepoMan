"""The six stages, wired. This is the module `web/` and `cli.py` call, and nothing else.

Swapping `pipeline_stub` for this file is the whole integration: the signatures are identical and
every stage writes to the same `Store` keys the UI already reads.

`status.json` is updated at every stage and once per requirement. That is not cosmetic — it is
what the evaluator watches while a submission runs, and the queue polls it every two seconds.
"""

from __future__ import annotations

from pathlib import Path

from repoman.core.resolver import Checkout
from repoman.core.types import (Precedent, Requirement, RunManifest, RunStatus, Rubric, Submission,
                                SubmissionIdentity, new_id, now)
from repoman.intake import IntakeError, acquire, find_readme
from repoman.probes import PROBE_VERSION, repomap, similarity
from repoman.probes.run import run_probes
from repoman.store import Store
from repoman.verify.claims import extract_claims, verify_claims
from repoman.verify.compile import compile_rubric as _compile  # noqa: F401  (re-exported below)
from repoman.verify.contradict import apply_contradictions
from repoman.verify.model import model_id
from repoman.verify.verify import verify_all


def compile_rubric(source_text: str, artifact_kinds: list[str]) -> Rubric:
    """Stage 03. Prose → an editable checklist the evaluator approves before anything runs."""
    return _compile(source_text, artifact_kinds)


def run_submission(store: Store, batch_id: str, rubric: Rubric, *, repo_url: str | None = None,
                   zip_path: str | None = None, dir_path: str | None = None, report_path: str | None = None,
                   deploy_url: str | None = None, event_window: tuple[str, str] | None = None,
                   precedents: list[Precedent] = (), run_id: str | None = None,
                   check_claims: bool = True, baseline: str | None = None, label: str | None = None) -> str:
    """Stages 01–05 for one submission. Blocking; `web/` calls it in a thread."""
    run_id = run_id or new_id()
    prefix = f"runs/{run_id}/"

    def status(stage, detail="", done=0, total=0):
        store.put_json(prefix + "status.json",
                       RunStatus(stage=stage, detail=detail, done=done, total=total))

    # --- 01–02 acquire ----------------------------------------------------------
    status("acquire", "cloning at HEAD" if repo_url else "unpacking the submission")
    run_dir = store.local_dir(prefix.rstrip("/"))
    try:
        acquired = acquire(Path(run_dir), batch_id=batch_id, submission_id=run_id, repo_url=repo_url,
                           zip_path=zip_path, dir_path=dir_path, report_path=report_path, deploy_url=deploy_url)
    except IntakeError as e:
        status("failed", str(e)[:200])
        raise

    sub: Submission = acquired.submission
    if label:  # the import folder's name (a student id, a team) — identity, kept apart so blind mode can strip it
        sub.identity = SubmissionIdentity(team=label)
    repo = Path(run_dir) / "repo"
    store.put_json(prefix + "submission.json", sub)
    store.put_json(prefix + "report_pages.json", acquired.pages)
    store.archive_dir(prefix + "repo", repo)  # no-op on LocalStore; a tarball on S3

    # --- 04 probe ---------------------------------------------------------------
    status("probe", "deps · tests · git · injection · deploy")
    report = run_probes(repo, sub, pages=acquired.pages, event_window=event_window, baseline=baseline)
    store.put_json(prefix + "probes.json", report.as_json())
    store.put_json(prefix + "fingerprint.json", report.fingerprint)
    store.put_json(prefix + "submission.json", sub)  # probes may have quarantined artifacts

    checkout = Checkout(root=repo, commitSha=sub.commitSha, pages=acquired.pages, captures=report.captures,
                        repo_map=repomap.build(repo, frozenset(report.quarantined)))

    manifest = RunManifest(
        id=run_id, submissionId=sub.id, rubricId=rubric.id, rubricVersion=rubric.version,
        commitSha=sub.commitSha, modelId=model_id(),
        artifactShas={a.uri: a.sha256 for a in sub.artifacts if a.sha256},
        probeVersions={name: PROBE_VERSION for name in report.results},
    )

    # --- 05a claims: what the submission says about itself, read once, before verification -----
    # Each claim is tagged with the requirement it is about, so that requirement's investigation
    # answers it from the same files. Only claims about nothing in the rubric get a run of their own.
    checkable: list[Requirement] = [r for r in rubric.requirements if r.verifiable]
    claims: list = []
    usage_extra: list = []
    mismatches_extra = 0
    if check_claims:
        status("claims", "reading what the submission says about itself", 0, 0)
        claims, usage, lost = extract_claims(checkout, submission_id=sub.id, requirements=checkable,
                                             quarantined=frozenset(report.quarantined))
        store.put_json(prefix + "claims.json", claims)
        if usage:
            usage_extra.append(usage)
        mismatches_extra += lost

    # --- 05 verify --------------------------------------------------------------
    status("verify", checkable[0].title if checkable else "", 0, len(checkable))
    titles = {r.id: r.title for r in checkable}
    so_far: list = []  # written after every finding so the run page fills while the rest are investigated

    def landed(finding):
        so_far.append(finding)
        store.put_json(prefix + "findings.json", so_far)

    outcome = verify_all(
        rubric.requirements, checkout, submission_id=sub.id, claims=claims,
        probe_summary=report.summary_for_prompt(), quarantined=frozenset(report.quarantined),
        precedents=list(precedents), flags=report.flags, on_finding=landed,
        on_progress=lambda done, total, rid: status("verify", titles.get(rid, ""), done, total),
    )
    outcome.usage = usage_extra + outcome.usage
    outcome.mismatches += mismatches_extra
    claim_findings = [f for f in outcome.findings if f.subject == "claim"]
    outcome.findings = [f for f in outcome.findings if f.subject != "claim"]
    store.put_json(prefix + "findings.json", outcome.findings + claim_findings)

    # claims about nothing in the rubric, and attached claims the agent left unanswered
    answered = {f.requirementId for f in claim_findings}
    orphans = [c.model_copy(update={"requirementId": None}) for c in claims if c.id not in answered]
    if orphans:
        claimed = verify_claims(
            orphans, checkout, submission_id=sub.id, probe_summary=report.summary_for_prompt(),
            quarantined=frozenset(report.quarantined), flags=report.flags, on_finding=landed,
            on_progress=lambda done, total, _cid: status("claims", f"claim {done} of {total}", done, total),
        )
        claim_findings += claimed.findings
        outcome.usage += claimed.usage
        outcome.mismatches += claimed.mismatches
        store.put_json(prefix + "findings.json", outcome.findings + claim_findings)

    # --- 05b contradiction ------------------------------------------------------
    status("contradict", "cross-checking the submission's own claims", len(checkable), len(checkable))
    readme = find_readme(repo)
    findings, extra_mismatches, note = apply_contradictions(
        outcome.findings, rubric.requirements, checkout, submission_id=sub.id,
        readme=readme.read_text(encoding="utf-8", errors="replace") if readme else "",
    )
    store.put_json(prefix + "findings.json", findings + claim_findings)

    manifest.usage = outcome.usage
    if note:
        manifest.notes.append(note)
    manifest.mismatches = outcome.mismatches + extra_mismatches
    manifest.finishedAt = now()
    store.put_json(prefix + "manifest.json", manifest)
    if not store.exists(prefix + "decisions.json"):
        store.put_json(prefix + "decisions.json", [])

    verified = sum(1 for f in findings if f.state == "VERIFIED")
    status("done", f"{verified} of {len(checkable)} requirements have verified evidence",
           len(checkable), len(checkable))
    return run_id


def expand_bulk_import(path: Path, extract_to: Path) -> list[tuple[Path, str]]:
    """One archive or one directory, many submissions: the shape DOMjudge, Moodle and GitHub Classroom
    export. Every top-level folder becomes one submission labelled by its own name; a loose file at the
    top level is wrapped as its own single-file submission. Shared by the web bulk-import form and the
    CLI's `repoman bulk` command, so both read a course export the same way.
    """
    from repoman.intake import unzip

    root = path
    if path.is_file():  # a .zip: unpack once, work from there
        root = extract_to
        unzip(path, root)
    entries = sorted(p for p in root.iterdir() if not p.name.startswith((".", "__MACOSX")))
    jobs: list[tuple[Path, str]] = []
    for entry in entries:
        if entry.is_dir():
            jobs.append((entry, entry.name))
        elif entry.is_file():
            single = root / f"_{entry.stem}"
            single.mkdir(exist_ok=True)
            entry.rename(single / entry.name)
            jobs.append((single, entry.stem))
    return jobs


def batch_similarity(store: Store, run_ids: list[str]) -> list[dict]:
    """Which submissions in a batch share identical files.

    Computed on demand rather than stored on a run: the answer changes as the batch fills, and a
    later submission must never rewrite an earlier one's findings — those are an audit record of
    what was true when a human looked at them.
    """
    fingerprints = {rid: store.get_json(f"runs/{rid}/fingerprint.json", {}) or {} for rid in run_ids}
    return [o.as_json() for o in similarity.compare(fingerprints)]
