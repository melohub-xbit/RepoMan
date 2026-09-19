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


def test_batch_page_counts_the_cohort(client):
    """Two done runs: the batch page says how many verified each requirement and which claims failed."""
    c, store = client
    from repoman.core.types import Batch, RunStatus, Submission

    # second submission: same rubric, tests VERIFIED, one claim that did not hold
    findings = store.get_json("runs/run1/findings.json")
    second = [{**findings[0], "id": "f9", "submissionId": "run2", "state": "VERIFIED"},
              {**findings[0], "id": "f10", "submissionId": "run2", "requirementId": "c9", "subject": "claim",
               "state": "CONTRADICTED", "summary": "Says five roles; two exist."}]
    claim = Claim(id="c9", submissionId="run2", statement="Five user roles are enforced.",
                  source=Evidence(id="src9", submissionId="run2", provenance="model", quote=README_LINE,
                                  locator=FileRange(path="README.md", startLine=3, endLine=3, commitSha=SHA)))
    store.put_json("runs/run2/submission.json", Submission(id="run2", batchId="b1", source="github",
                                                            repoUrl="https://github.com/team/other", commitSha=SHA))
    store.put_json("runs/run2/findings.json", second)
    store.put_json("runs/run2/claims.json", [claim])
    store.put_json("runs/run2/decisions.json", [])
    store.put_json("runs/run2/status.json", RunStatus(stage="done", done=1, total=1))
    store.put_json("runs/run2/probes.json", {})
    b = Batch.model_validate(store.get_json("batches/b1.json")); b.runIds.append("run2")
    store.put_json("batches/b1.json", b)

    page = c.get("/batches/b1").text
    assert "Across the batch" in page and "1 of 2 partial" in page  # run1 PARTIAL, run2 VERIFIED
    assert "0 of 1" in page and "Five user roles are enforced." in page
    assert "#said-c9" in page


def test_run_page_fills_while_running(client):
    c, store = client
    from repoman.core.types import RunStatus

    store.put_json("runs/run1/status.json", RunStatus(stage="verify", detail="Automated tests", done=1, total=2))
    page = c.get("/runs/run1").text
    assert page.count('id="findings"') == 1 and page.count('id="reqindex"') == 1 and 'id="live"' in page
    live = c.get("/runs/run1/live")
    assert live.status_code == 200 and 'hx-swap-oob="true"' in live.text and "Two test files" in live.text
    store.put_json("runs/run1/status.json", RunStatus(stage="done", done=2, total=2))
    done = c.get("/runs/run1/live")
    assert done.headers.get("HX-Refresh") == "true"
    assert 'id="live"' not in c.get("/runs/run1").text


def test_attached_claims_are_answered_in_the_requirements_investigation(monkeypatch):
    """One agent run, two findings: the requirement's and the claim's, sharing resolved evidence."""
    from repoman.core.types import ClaimVerdict, Requirement

    draft = FindingDraft(state="VERIFIED", summary="Two roles, enforced on /admin.", confidence="high",
                         confidenceReason="Read the enum and the config.",
                         evidence=[LocatorDraft(locator=FileRange(path="src/main/java/app/model/UserRole.java", startLine=7,
                                                                  endLine=10, commitSha="x"), quote="public enum UserRole {")],
                         claimVerdicts=[ClaimVerdict(claimId="c5", state="CONTRADICTED", summary="Two roles exist, not five.")])
    seen = {}
    def ask(req, box, precedents, claims=()):
        seen["claims"] = list(claims)
        seen["turn"] = V.build_turn(req, box, precedents, claims)
        return draft, None
    monkeypatch.setattr(V, "_ask", ask)
    claim = Claim(id="c5", submissionId="s", statement="Five user roles are enforced.", requirementId="q2",
                  source=Evidence(submissionId="s", provenance="model", quote=README_LINE,
                                  locator=FileRange(path="README.md", startLine=3, endLine=3, commitSha=SHA)))
    req = Requirement(id="q2", rubricId="rb", title="RBAC", statement="Roles are defined and enforced.", weight=20, verifiable=True)
    out = V.verify_all([req], Checkout(root=REPO, commitSha=SHA), submission_id="s", claims=[claim])
    assert [c.id for c in seen["claims"]] == ["c5"] and "[c5] Five user roles are enforced." in seen["turn"]
    assert [(f.subject, f.state) for f in out.findings] == [("requirement", "VERIFIED"), ("claim", "CONTRADICTED")]
    cf = out.findings[1]
    assert cf.requirementId == "c5" and cf.evidence == out.findings[0].evidence and "not five" in cf.summary


def test_orphan_claims_are_the_only_ones_verify_claims_runs(monkeypatch):
    calls = []
    monkeypatch.setattr(C, "verify_all", lambda pseudo, *a, **k: (calls.append([r.id for r in pseudo]) or V.VerifyOutcome()))
    src = Evidence(submissionId="s", provenance="model", quote=README_LINE,
                   locator=FileRange(path="README.md", startLine=3, endLine=3, commitSha=SHA))
    claims = [Claim(id="a", submissionId="s", statement="x", source=src, requirementId="q1"),
              Claim(id="b", submissionId="s", statement="y", source=src)]
    C.verify_claims(claims, Checkout(root=REPO, commitSha=SHA), submission_id="s")
    assert calls == [["b"]]
