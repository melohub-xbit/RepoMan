"""Stand-in for repoman.pipeline until the engine lands. Same signatures.

Copies fixtures/sample_run into a new run, walking status.json through the stages
so the UI's live queue can be built against real timing. Delete when pipeline.py exists.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from repoman.core.types import Precedent, Requirement, Rubric, RunStatus, SourceSpan
from repoman.store import Store

SAMPLE = Path(__file__).resolve().parent.parent / "fixtures" / "sample_run"


def compile_rubric(source_text: str, artifact_kinds: list[str]) -> Rubric:
    time.sleep(1.5)
    sample = Rubric.model_validate(json.loads((SAMPLE / "rubric.json").read_text()))
    rubric = Rubric(sourceText=source_text, compiledBy="stub")
    rubric.requirements = [Requirement(**{**r.model_dump(), "id": f"q{i+1}", "rubricId": rubric.id})
                           for i, r in enumerate(sample.requirements)]
    return rubric


def run_submission(store: Store, batch_id: str, rubric: Rubric, *, repo_url: str | None = None,
                   zip_path: str | None = None, report_path: str | None = None, deploy_url: str | None = None,
                   event_window: tuple[str, str] | None = None, precedents: list[Precedent] = (),
                   run_id: str | None = None) -> str:
    import shutil

    from repoman.core.types import new_id

    run_id = run_id or new_id()
    prefix = f"runs/{run_id}/"
    sub = json.loads((SAMPLE / "submission.json").read_text())
    sub["batchId"] = batch_id
    if repo_url:
        sub["repoUrl"] = repo_url
    store.put_json(prefix + "submission.json", sub)

    def status(stage, detail="", done=0, total=0):
        store.put_json(prefix + "status.json", RunStatus(stage=stage, detail=detail, done=done, total=total))

    status("acquire", "cloning at HEAD")
    time.sleep(2)
    if (SAMPLE / "repo").exists():
        shutil.copytree(SAMPLE / "repo", store.local_dir(prefix + "repo"), dirs_exist_ok=True)
    status("probe", "deps · tests · git · injection · deploy")
    time.sleep(2)
    store.put_json(prefix + "probes.json", json.loads((SAMPLE / "probes.json").read_text()))
    store.put_json(prefix + "report_pages.json", json.loads((SAMPLE / "report_pages.json").read_text()))
    reqs = [r for r in rubric.requirements if r.verifiable]
    findings = json.loads((SAMPLE / "findings.json").read_text())
    for i, r in enumerate(reqs):
        status("verify", r.title, i, len(reqs))
        time.sleep(1.5)
    # remap fixture findings onto this rubric's requirement ids by position
    for f, r in zip(findings, reqs):
        f["requirementId"] = r.id
        f["submissionId"] = sub["id"]
    status("contradict", "cross-checking report claims", len(reqs), len(reqs))
    time.sleep(1.5)
    store.put_json(prefix + "findings.json", findings[: len(reqs)])
    manifest = json.loads((SAMPLE / "manifest.json").read_text())
    manifest.update({"id": run_id, "rubricId": rubric.id, "submissionId": sub["id"]})
    store.put_json(prefix + "manifest.json", manifest)
    store.put_json(prefix + "decisions.json", [])
    status("done", f"{len(reqs)} requirements", len(reqs), len(reqs))
    return run_id
