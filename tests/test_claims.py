"""The claims ledger: the submission's own description, extracted with a locator and checked like a rubric line."""

from __future__ import annotations

from pathlib import Path

from repoman.core.resolver import Checkout
from repoman.core.types import Claim, Evidence, FileRange, FindingDraft, LocatorDraft
from tests.test_web import client  # noqa: F401
from repoman.verify import claims as C
from repoman.verify import verify as V

SHA = "9f3c2a1d7e4b8c0a5d6e7f8091a2b3c4d5e6f708"
REPO = Path(__file__).resolve().parent.parent / "fixtures" / "sample_run" / "repo"
README_LINE = ("Task management API with JWT authentication, full role-based access control, Redis caching "
               "and a comprehensive test suite.")


def source(quote=README_LINE, line=3) -> LocatorDraft:
    return LocatorDraft(locator=FileRange(path="README.md", startLine=line, endLine=line, commitSha="x"), quote=quote)


def test_extracted_claims_keep_only_sentences_that_resolve(monkeypatch):
    draft = C.ClaimsDraft(claims=[
        C.ClaimDraft(statement="Redis is used as a cache.", source=source()),
        C.ClaimDraft(statement="Deployed on Kubernetes.", source=source(quote="runs on a Kubernetes cluster")),
    ])
    monkeypatch.setattr(C, "_ask", lambda box, turn: (draft, None))
    claims, usage, dropped = C.extract_claims(Checkout(root=REPO, commitSha=SHA), submission_id="s")
    assert [c.statement for c in claims] == ["Redis is used as a cache."]
    assert dropped == 1 and usage is None
    assert claims[0].source.locator.commitSha == SHA and claims[0].source.quote == README_LINE


def test_claim_findings_are_tagged_and_never_count_as_coverage(monkeypatch):
    from repoman.core.types import Requirement, coverage

    good = FindingDraft(state="UNVERIFIED", summary="No Redis usage anywhere.", confidence="medium",
                        confidenceReason="Searched the tree.",
                        evidence=[LocatorDraft(locator=FileRange(path="pom.xml", startLine=1, endLine=60, commitSha="x"),
                                               quote="spring-boot-starter-data-redis")])
    monkeypatch.setattr(V, "_ask", lambda *a, **k: (good, None))
    claim = Claim(id="c1", submissionId="s", statement="Redis is used as a cache.",
                  source=Evidence(submissionId="s", provenance="model", quote=README_LINE,
                                  locator=FileRange(path="README.md", startLine=3, endLine=3, commitSha=SHA)))
    out = C.verify_claims([claim], Checkout(root=REPO, commitSha=SHA), submission_id="s")
    f = out.findings[0]
    assert f.subject == "claim" and f.requirementId == "c1" and f.state == "UNVERIFIED"
    rubric_req = Requirement(id="q1", rubricId="rb", title="Caching", statement="A cache is used.", weight=10, verifiable=True)
    assert coverage(out.findings, [rubric_req]) == (0, 1)


def test_run_page_shows_what_they_said(client):
    c, store = client
    claim = Claim(id="c1", submissionId="run1", statement="Redis is used as a cache.",
                  source=Evidence(id="src1", submissionId="run1", provenance="model", quote=README_LINE,
                                  locator=FileRange(path="README.md", startLine=3, endLine=3, commitSha=SHA)))
    store.put_json("runs/run1/claims.json", [claim])
    findings = store.get_json("runs/run1/findings.json")
    findings.append({**findings[0], "id": "f2", "requirementId": "c1", "subject": "claim", "state": "UNVERIFIED",
                     "summary": "Declared, never imported."})
    store.put_json("runs/run1/findings.json", findings)
    page = c.get("/runs/run1").text
    assert "What they said they built" in page and "Redis is used as a cache." in page
    assert "Looked · not found" in page
    # the claim's own sentence opens in the evidence pane like any citation
    r = c.get("/runs/run1/evidence/src1")
    assert r.status_code == 200 and "Redis caching" in r.text
    # coverage on the queue is untouched by claim findings
    assert "<b>0</b> of 1" in c.get("/batches/b1").text


def test_batch_option_controls_the_pass(client):
    c, store = client
    from repoman.core.types import Batch

    r = c.post("/batches", data={"name": "No claims"}, follow_redirects=False)
    bid = r.headers["location"].split("/")[2]
    assert Batch.model_validate(store.get_json(f"batches/{bid}.json")).checkClaims is False
    r = c.post("/batches", data={"name": "Claims", "claims": "on"}, follow_redirects=False)
    bid = r.headers["location"].split("/")[2]
    assert Batch.model_validate(store.get_json(f"batches/{bid}.json")).checkClaims is True
