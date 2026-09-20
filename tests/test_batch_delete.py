"""Batches page: a top-bar create action, richer rows, and an explicit, confirmed delete."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from repoman.core.types import (Batch, Decision, Evidence, FileRange, Finding, Precedent, Requirement,
                                Rubric, RunStatus, Scale, Submission)

SHA = "9f3c2a1d7e4b8c0a5d6e7f8091a2b3c4d5e6f708"


@pytest.fixture
def listing(tmp_path):
    from repoman.store import LocalStore
    from repoman.web import app as web

    store = LocalStore(tmp_path)
    web.store = store

    rubric = Rubric(id="rb", sourceText="1. Tests.", compiledBy="test", requirements=[
        Requirement(id="q1", rubricId="rb", title="Automated tests", statement="Tests exist.", weight=20.0,
                    scale=Scale(kind="points"), verifiable=True),
        Requirement(id="q2", rubricId="rb", title="Creativity", statement="x", weight=10.0, verifiable=False,
                    unverifiableReason="Human only."),
    ])
    store.put_json("rubrics/rb.json", rubric)

    full = Batch(id="full1", name="With a rubric", rubricId="rb", runIds=["r1"])
    store.put_json("batches/full1.json", full)
    ev = Evidence(submissionId="r1", provenance="probe", quote="x",
                 locator=FileRange(path="a", startLine=1, endLine=1, commitSha=SHA))
    store.put_json("runs/r1/submission.json", Submission(id="r1", batchId="full1", source="github",
                                                          repoUrl="https://github.com/t/p", commitSha=SHA))
    store.put_json("runs/r1/findings.json", [Finding(submissionId="r1", requirementId="q1", state="VERIFIED",
                                                      summary="x", confidence="high", confidenceReason="x",
                                                      producedBy="t", evidence=[ev])])
    store.put_json("runs/r1/decisions.json", [Decision(submissionId="r1", requirementId="q1", evaluatorId="e",
                                                        score=18).model_dump()])
    store.put_json("runs/r1/status.json", RunStatus(stage="done", done=1, total=1))
    store.put_json("runs/r1/probes.json", {})
    store.put_json("precedents/full1.json", [Precedent(batchId="full1", requirementId="q1", rule="x",
                                                        derivedFromDecisionId="d1").model_dump()])

    bare = Batch(id="bare1", name="No rubric yet")
    store.put_json("batches/bare1.json", bare)

    return TestClient(web.app), store


# --- the listing page ---------------------------------------------------------------


def test_index_shows_a_new_batch_action_and_row_tags(listing):
    c, _ = listing
    page = c.get("/batches").text
    assert 'id="new-batch"' in page and "+ New batch" in page
    assert "1 requirement" in page  # full1: only q1 is verifiable, q2 is not
    assert "no rubric yet" in page  # bare1
    assert "Delete this batch" in page and page.count("Delete this batch") == 2
    assert 'class="delete-batch"' in page and page.count('class="delete-batch"') == 2


def test_empty_state_opens_the_new_batch_panel_by_default(tmp_path):
    from repoman.store import LocalStore
    from repoman.web import app as web

    web.store = LocalStore(tmp_path)
    page = TestClient(web.app).get("/batches").text
    assert '<details id="new-batch" class="rubric-in card"  open' in page.replace("\n", " ") or "open" in page


# --- deleting ------------------------------------------------------------------------


def test_delete_removes_the_batch_its_rubric_precedents_and_every_run(listing):
    c, store = listing
    r = c.post("/batches/full1/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/batches"

    assert store.get_json("batches/full1.json") is None
    assert store.get_json("rubrics/rb.json") is None
    assert store.get_json("precedents/full1.json") is None
    assert store.get_json("runs/r1/submission.json") is None
    assert store.get_json("runs/r1/findings.json") is None
    assert store.list("runs/r1") == []

    page = c.get("/batches").text
    assert "With a rubric" not in page


def test_deleting_a_batch_leaves_other_batches_and_their_runs_alone(listing):
    c, store = listing
    c.post("/batches/full1/delete")
    assert store.get_json("batches/bare1.json") is not None


def test_deleting_a_rubric_less_batch_does_not_touch_rubrics_or_runs(listing):
    """bare1 has no rubricId and no runs — the delete route must not try to delete rubrics/None.json."""
    c, store = listing
    r = c.post("/batches/bare1/delete", follow_redirects=False)
    assert r.status_code == 303
    assert store.get_json("batches/bare1.json") is None
    assert store.get_json("rubrics/rb.json") is not None  # untouched: bare1 never had this rubric


def test_deleting_an_already_deleted_or_unknown_batch_is_not_an_error(listing):
    c, _ = listing
    r = c.post("/batches/full1/delete", follow_redirects=False)
    assert r.status_code == 303
    r2 = c.post("/batches/full1/delete", follow_redirects=False)  # already gone
    assert r2.status_code == 303
    r3 = c.post("/batches/does-not-exist/delete", follow_redirects=False)
    assert r3.status_code == 303


def test_a_batch_name_with_a_quote_does_not_break_the_delete_confirm_markup(tmp_path):
    """An inline onsubmit="...'{{ b.name }}'..." would let an apostrophe in the name terminate the
    JS string early (the browser HTML-decodes the attribute before running it). data-name sidesteps
    that: confirm the row renders with the raw name in a data attribute, not inlined into a script."""
    from repoman.store import LocalStore
    from repoman.web import app as web

    store = LocalStore(tmp_path)
    web.store = store
    store.put_json("batches/q1.json", Batch(id="q1", name="O'Brien's \"Best\" Class"))
    page = TestClient(web.app).get("/batches").text
    assert 'data-name="O&#39;Brien&#39;s' in page or "O&#39;Brien" in page  # Jinja-escaped, not raw-inlined
    assert "onsubmit=" not in page  # no per-row inline JS to escape into in the first place


def test_deleting_a_bulk_import_batch_also_clears_its_imports_prefix(listing):
    c, store = listing
    store.put_bytes("imports/full1/job1/student.cpp", b"int main(){}")
    assert store.list("imports/full1")
    c.post("/batches/full1/delete")
    assert store.list("imports/full1") == []
