"""The evaluator workspace, end to end against a store on disk.

These exist because a template that only breaks once a batch has rows in it will pass every unit
test and fail in front of a judge. Every page an evaluator touches during a grading pass is
requested here, with realistic data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from repoman.core.types import (Batch, Evidence, FileRange, Finding, Requirement, Rubric, RunStatus,
                                Scale, Submission)

FIXTURE_REPO = Path(__file__).resolve().parent.parent / "fixtures" / "sample_run" / "repo"
SHA = "9f3c2a1d7e4b8c0a5d6e7f8091a2b3c4d5e6f708"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from repoman.store import LocalStore
    from repoman.web import app as web

    store = LocalStore(tmp_path)
    monkeypatch.setattr(web, "store", store)

    rubric = Rubric(id="rb", sourceText="1. Tests — 20 marks.\n2. Creativity — 15 marks.",
                    compiledBy="test", requirements=[
                        Requirement(id="q1", rubricId="rb", title="Automated tests",
                                    statement="Tests exist and assert something.", weight=20.0,
                                    scale=Scale(kind="points"), verifiable=True),
                        Requirement(id="q2", rubricId="rb", title="Creativity",
                                    statement="How imaginative it is.", weight=15.0,
                                    scale=Scale(kind="check"), verifiable=False,
                                    unverifiableReason="Nothing in a repository shows imagination."),
                    ])
    batch = Batch(id="b1", name="CS402", rubricId="rb", runIds=["run1"])
    sub = Submission(id="run1", batchId="b1", source="github",
                     repoUrl="https://github.com/team/taskflow", commitSha=SHA)
    finding = Finding(
        id="f1", submissionId="run1", requirementId="q1", state="PARTIAL",
        flagged=["PROMPT_INJECTION"], summary="Two test files; one asserts nothing.",
        confidence="medium", confidenceReason="Read both files.", producedBy="test",
        questions=["Which tests cover the ordering flow?"],
        evidence=[Evidence(id="e1", submissionId="run1", provenance="model",
                           quote="void contextLoads() {",
                           locator=FileRange(path="src/test/java/app/TaskflowApplicationTests.java",
                                             startLine=12, endLine=13, commitSha=SHA))])

    store.put_json("rubrics/rb.json", rubric)
    store.put_json("batches/b1.json", batch)
    store.put_json("runs/run1/submission.json", sub)
    store.put_json("runs/run1/findings.json", [finding])
    store.put_json("runs/run1/decisions.json", [])
    store.put_json("runs/run1/status.json", RunStatus(stage="done", done=1, total=1))
    store.put_json("runs/run1/probes.json", {"deps": {"summary": "One unused.", "evidence": []}})
    store.put_json("runs/run1/report_pages.json", {})

    # a real checkout so the evidence pane has bytes to render
    dest = store.local_dir("runs/run1/repo")
    for src in FIXTURE_REPO.rglob("*"):
        if src.is_file():
            target = dest / src.relative_to(FIXTURE_REPO)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(src.read_bytes())

    return TestClient(web.app), store


# --- every page in a grading pass --------------------------------------------------


@pytest.mark.parametrize("path", [
    "/", "/batches", "/batches/b1", "/batches/b1/rubric", "/runs/run1",
    "/runs/run1/facts", "/runs/run1/status", "/runs/run1/evidence/e1",
    "/batches/b1/export.csv", "/runs/run1/packet.md", "/runs/run1/feedback.md",
])
def test_page_renders(client, path):
    c, _store = client
    assert c.get(path).status_code == 200, path


def test_the_queue_shows_coverage_as_a_count_not_a_percentage(client):
    c, _ = client
    body = c.get("/batches/b1").text
    assert "0 of 1" in body or "<b>0</b> of 1" in body
    assert "%" not in body.split("Evidence found")[-1][:200]


def test_the_workspace_shows_the_finding_and_its_evidence(client):
    c, _ = client
    body = c.get("/runs/run1").text
    assert "Two test files; one asserts nothing." in body
    assert "TaskflowApplicationTests" in body
    assert "PARTIAL" in body


def test_an_unverifiable_requirement_reads_as_the_humans(client):
    c, _ = client
    assert "Stays with you" in c.get("/runs/run1").text


def test_flags_are_surfaced_on_the_workspace(client):
    c, _ = client
    assert "prompt injection" in c.get("/runs/run1").text.lower()


def test_the_evidence_pane_renders_the_cited_lines(client):
    c, _ = client
    body = c.get("/runs/run1/evidence/e1").text
    assert "contextLoads" in body
    assert "line cited" in body  # the cited range is marked, not just shown


def test_a_missing_evidence_id_is_a_404_not_a_crash(client):
    c, _ = client
    assert c.get("/runs/run1/evidence/nope").status_code == 404


# --- the human's contribution -------------------------------------------------------


def test_recording_a_decision_writes_it_and_re_renders_the_card(client):
    c, store = client
    r = c.post("/runs/run1/decisions",
               data={"requirement_id": "q1", "action": "accept", "score": "15", "note": "fair"})
    assert r.status_code == 200
    decisions = store.get_json("runs/run1/decisions.json", [])
    assert len(decisions) == 1 and decisions[0]["score"] == 15.0
    assert "fair" in r.text


def test_an_override_can_become_a_precedent_for_the_batch(client):
    c, store = client
    c.post("/runs/run1/decisions",
           data={"requirement_id": "q1", "action": "override", "score": "20", "note": "",
                 "precedent": "accept config as caching evidence"})
    precedents = store.get_json("precedents/b1.json", [])
    assert len(precedents) == 1
    assert precedents[0]["rule"] == "accept config as caching evidence"


def test_deciding_twice_replaces_rather_than_duplicates(client):
    c, store = client
    c.post("/runs/run1/decisions", data={"requirement_id": "q1", "action": "accept", "score": "10"})
    c.post("/runs/run1/decisions", data={"requirement_id": "q1", "action": "accept", "score": "18"})
    decisions = store.get_json("runs/run1/decisions.json", [])
    assert len(decisions) == 1 and decisions[0]["score"] == 18.0


def test_the_csv_carries_one_row_per_requirement(client):
    c, _ = client
    rows = [r for r in c.get("/batches/b1/export.csv").text.splitlines() if r.strip()]
    assert len(rows) == 3  # header + two requirements


def test_feedback_is_empty_until_the_evaluator_has_decided(client):
    c, _ = client
    assert "nothing to send" in c.get("/runs/run1/feedback.md").text
    c.post("/runs/run1/decisions", data={"requirement_id": "q1", "action": "accept", "score": "15"})
    body = c.get("/runs/run1/feedback.md").text
    assert "Automated tests" in body and "15 of 20" in body


def test_feedback_never_leaks_repomans_state_vocabulary(client):
    """The student-facing note is the evaluator's words, not RepoMan's machinery."""
    c, _ = client
    c.post("/runs/run1/decisions", data={"requirement_id": "q1", "action": "accept", "score": "15"})
    body = c.get("/runs/run1/feedback.md").text
    for jargon in ("PARTIAL", "UNVERIFIED", "confidence", "PROMPT_INJECTION"):
        assert jargon not in body


# --- guards --------------------------------------------------------------------------


def test_submissions_are_refused_until_a_rubric_is_approved(client, tmp_path):
    c, store = client
    store.put_json("batches/b2.json", Batch(id="b2", name="No rubric"))
    r = c.post("/batches/b2/submissions", data={"repo_url": "https://github.com/x/y"},
               follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/rubric")
    assert store.get_json("batches/b2.json")["runIds"] == []


def test_a_run_with_no_status_file_is_queued_not_a_500(client, tmp_path):
    c, store = client
    store.put_json("runs/run2/submission.json",
                   Submission(id="run2", batchId="b1", source="github", commitSha=SHA))
    store.put_json("batches/b1.json", Batch(id="b1", name="CS402", rubricId="rb",
                                            runIds=["run1", "run2"]))
    assert c.get("/batches/b1").status_code == 200
    assert c.get("/runs/run2").status_code == 200


# --- cross-submission similarity in the UI -------------------------------------------


def _add_twin(store, run_id="run3", shared=("src/A.java", "src/B.java", "src/C.java", "src/D.java")):
    """A second submission whose files hash identically to run1's."""
    prints = {p: f"hash{i}" for i, p in enumerate(shared)}
    store.put_json("runs/run1/fingerprint.json", prints)
    store.put_json(f"runs/{run_id}/fingerprint.json", prints)
    store.put_json(f"runs/{run_id}/submission.json",
                   Submission(id=run_id, batchId="b1", source="github",
                              repoUrl="https://github.com/other/twin", commitSha=SHA))
    store.put_json(f"runs/{run_id}/status.json", RunStatus(stage="done"))
    store.put_json(f"runs/{run_id}/findings.json", [])
    store.put_json(f"runs/{run_id}/decisions.json", [])
    store.put_json("batches/b1.json", Batch(id="b1", name="CS402", rubricId="rb",
                                            runIds=["run1", run_id]))


def test_the_batch_page_pairs_submissions_that_share_code(client):
    c, store = client
    _add_twin(store)
    body = c.get("/batches/b1").text
    assert "Shared code across this batch" in body
    assert "other/twin" in body
    assert "4 identical" in body


def test_the_batch_page_says_shared_code_is_not_a_verdict(client):
    """Identical files can mean copying, a shared template, or the same tutorial."""
    c, store = client
    _add_twin(store)
    assert "does not tell them apart" in c.get("/batches/b1").text


def test_a_submission_shows_what_it_shares_and_links_to_the_other(client):
    c, store = client
    _add_twin(store)
    body = c.get("/runs/run1").text
    assert "identical to" in body and 'href="/runs/run3"' in body


def test_submissions_that_share_nothing_are_not_paired(client):
    c, store = client
    store.put_json("runs/run1/fingerprint.json", {"a.java": "h1", "b.java": "h2"})
    _add_twin(store, shared=("x.java", "y.java", "z.java", "w.java"))
    store.put_json("runs/run1/fingerprint.json", {"a.java": "h1", "b.java": "h2"})
    body = c.get("/batches/b1").text
    assert "Shared code across this batch" not in body


def test_a_fork_flag_is_explained_not_just_chipped(client):
    """A flag with no sentence under it is an accusation the evaluator cannot check."""
    c, store = client
    finding = Finding.model_validate(store.get_json("runs/run1/findings.json")[0])
    finding.flagged = ["FORK_SUSPECTED"]
    store.put_json("runs/run1/findings.json", [finding])
    store.put_json("runs/run1/probes.json", {
        "fork": {"summary": "Origin: the first commit already contains 100% of the files.",
                 "evidence": []}})
    body = c.get("/runs/run1").text
    assert "fork suspected" in body.lower()
    assert "first commit already contains" in body


def test_a_run_interrupted_by_a_restart_is_closed_out(client):
    """A pipeline runs in a daemon thread. Stopping the server kills it mid-stage, and nothing
    will ever write to its status again — so the queue must not poll it forever."""
    c, store = client
    store.put_json("runs/run1/status.json", RunStatus(stage="verify", done=2, total=5))
    from repoman.web import app as web

    web.close_out_interrupted_runs()
    status = store.get_json("runs/run1/status.json")
    assert status["stage"] == "failed" and "interrupted during verify" in status["detail"]


def test_the_sweep_leaves_finished_runs_alone(client):
    c, store = client
    web_status = store.get_json("runs/run1/status.json")
    assert web_status["stage"] == "done"
    from repoman.web import app as web

    web.close_out_interrupted_runs()
    assert store.get_json("runs/run1/status.json")["stage"] == "done"


def test_the_app_never_falls_back_to_fixture_findings():
    """The stub fallback is gone: a broken engine must fail loudly, not serve invented evidence."""
    from repoman.web import app as web

    assert web.pipeline.__name__ == "repoman.pipeline"
    assert not Path(web.__file__).parent.parent.joinpath("pipeline_stub.py").exists()
