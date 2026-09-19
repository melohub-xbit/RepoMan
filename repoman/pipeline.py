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
                                new_id, now)
from repoman.intake import IntakeError, acquire, find_readme
from repoman.probes import PROBE_VERSION, similarity
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
                   zip_path: str | None = None, report_path: str | None = None,
                   deploy_url: str | None = None, event_window: tuple[str, str] | None = None,
                   precedents: list[Precedent] = (), run_id: str | None = None,
                   check_claims: bool = True) -> str:
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
                           zip_path=zip_path, report_path=report_path, deploy_url=deploy_url)
    except IntakeError as e:
        status("failed", str(e)[:200])
        raise

    sub: Submission = acquired.submission
    repo = Path(run_dir) / "repo"
    store.put_json(prefix + "submission.json", sub)
    store.put_json(prefix + "report_pages.json", acquired.pages)
    store.archive_dir(prefix + "repo", repo)  # no-op on LocalStore; a tarball on S3

    # --- 04 probe ---------------------------------------------------------------
    status("probe", "deps · tests · git · injection · deploy")
    report = run_probes(repo, sub, pages=acquired.pages, event_window=event_window)
    store.put_json(prefix + "probes.json", report.as_json())
    store.put_json(prefix + "fingerprint.json", report.fingerprint)
    store.put_json(prefix + "submission.json", sub)  # probes may have quarantined artifacts

    checkout = Checkout(root=repo, commitSha=sub.commitSha, pages=acquired.pages,
                        captures=report.captures)

    manifest = RunManifest(
        id=run_id, submissionId=sub.id, rubricId=rubric.id, rubricVersion=rubric.version,
        commitSha=sub.commitSha, modelId=model_id(),
        artifactShas={a.uri: a.sha256 for a in sub.artifacts if a.sha256},
        probeVersions={name: PROBE_VERSION for name in report.results},
    )

    # --- 05 verify --------------------------------------------------------------
    checkable: list[Requirement] = [r for r in rubric.requirements if r.verifiable]
    status("verify", checkable[0].title if checkable else "", 0, len(checkable))

    outcome = verify_all(
        rubric.requirements, checkout, submission_id=sub.id,
        probe_summary=report.summary_for_prompt(), quarantined=frozenset(report.quarantined),
        precedents=list(precedents), flags=report.flags,
        on_progress=lambda done, total, _rid: status("verify", _next_title(checkable, done), done, total),
    )
    store.put_json(prefix + "findings.json", outcome.findings)

    # --- 05a claims: the submission's own description, checked the same way ------
    claim_findings = []
    if check_claims:
        status("claims", "reading what the submission says about itself", 0, 0)
        claims, usage, lost = extract_claims(checkout, submission_id=sub.id,
                                             quarantined=frozenset(report.quarantined))
        store.put_json(prefix + "claims.json", claims)
        if usage:
            outcome.usage.append(usage)
        outcome.mismatches += lost
        if claims:
            claimed = verify_claims(
                claims, checkout, submission_id=sub.id, probe_summary=report.summary_for_prompt(),
                quarantined=frozenset(report.quarantined), flags=report.flags,
                on_progress=lambda done, total, _cid: status("claims", f"claim {done} of {total}", done, total),
            )
            claim_findings = claimed.findings
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


def _next_title(checkable: list[Requirement], done: int) -> str:
    return checkable[done].title if done < len(checkable) else "finishing"


def batch_similarity(store: Store, run_ids: list[str]) -> list[dict]:
    """Which submissions in a batch share identical files.

    Computed on demand rather than stored on a run: the answer changes as the batch fills, and a
    later submission must never rewrite an earlier one's findings — those are an audit record of
    what was true when a human looked at them.
    """
    fingerprints = {rid: store.get_json(f"runs/{rid}/fingerprint.json", {}) or {} for rid in run_ids}
    return [o.as_json() for o in similarity.compare(fingerprints)]
