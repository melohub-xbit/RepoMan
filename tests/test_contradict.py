"""The cross-check that catches a submission claiming more than its code shows.

This file exists because the pass had no tests, failed silently on two real runs, and nobody
found out — the demo's most persuasive beat quietly not happening, recorded nowhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repoman.core.resolver import Checkout
from repoman.core.types import (Contradictions, ContradictionDraft, Evidence, FileRange, Finding,
                                LocatorDraft, Requirement, Scale)
from repoman.verify import contradict as C

REPO = Path(__file__).resolve().parent.parent / "fixtures" / "sample_run" / "repo"
SHA = "9f3c2a1d7e4b"
README = "Task management API with JWT authentication, full role-based access control, Redis caching."


@pytest.fixture
def setup():
    checkout = Checkout(root=REPO, commitSha=SHA, pages={"9": "RBAC across all five user tiers"})
    reqs = [Requirement(id="q1", rubricId="rb", title="Caching", statement="A caching layer is used.",
                        weight=10.0, scale=Scale(), verifiable=True)]
    findings = [Finding(
        id="f1", submissionId="s", requirementId="q1", state="VERIFIED",
        summary="Redis is declared in pom.xml.", confidence="high", confidenceReason="Read it.",
        producedBy="test",
        evidence=[Evidence(submissionId="s", provenance="probe", quote="spring-boot-starter-data-redis",
                           locator=FileRange(path="pom.xml", startLine=49, endLine=49, commitSha=SHA))])]
    return findings, reqs, checkout


def fake_agent(monkeypatch, result=None, raises=None):
    class _Agent:
        def __init__(self, **kw):
            pass

        def structured_output(self, model, prompt):
            if raises:
                raise raises
            return result

    monkeypatch.setattr(C, "Agent", _Agent)
    monkeypatch.setattr(C, "make_model", lambda: object())


def test_a_supported_contradiction_upgrades_the_finding(monkeypatch, setup):
    findings, reqs, checkout = setup
    fake_agent(monkeypatch, Contradictions(contradictions=[ContradictionDraft(
        requirementId="q1", summary="The README claims Redis caching; no code uses it.",
        evidence=[LocatorDraft(locator={"page": 9}, quote="RBAC across all five user tiers")])]))

    out, mismatches, note = C.apply_contradictions(findings, reqs, checkout, submission_id="s",
                                                   readme=README)
    assert out[0].state == "CONTRADICTED" and note is None and mismatches == 0
    assert len(out[0].evidence) == 2  # the code evidence AND the claim that disagrees with it
    assert "claims Redis caching" in out[0].summary
    assert out[0].questions  # the evaluator gets something to ask the team


def test_an_unresolvable_contradiction_is_not_applied(monkeypatch, setup):
    """Same rule as everywhere else: a claim whose quote is not there does not become a state."""
    findings, reqs, checkout = setup
    fake_agent(monkeypatch, Contradictions(contradictions=[ContradictionDraft(
        requirementId="q1", summary="Invented.",
        evidence=[LocatorDraft(locator={"page": 9}, quote="this sentence is on no page")])]))

    out, mismatches, _ = C.apply_contradictions(findings, reqs, checkout, submission_id="s",
                                                readme=README)
    assert out[0].state == "VERIFIED" and mismatches == 1


def test_an_unverified_finding_is_never_upgraded(monkeypatch, setup):
    """There is nothing to contradict if we never found anything in the first place."""
    findings, reqs, checkout = setup
    findings[0].state = "UNVERIFIED"
    fake_agent(monkeypatch, Contradictions(contradictions=[ContradictionDraft(
        requirementId="q1", summary="x",
        evidence=[LocatorDraft(locator={"page": 9}, quote="RBAC across all five user tiers")])]))

    out, _m, _n = C.apply_contradictions(findings, reqs, checkout, submission_id="s", readme=README)
    assert out[0].state == "UNVERIFIED"


def test_no_contradictions_is_the_common_correct_answer(monkeypatch, setup):
    findings, reqs, checkout = setup
    fake_agent(monkeypatch, Contradictions(contradictions=[]))
    out, _m, note = C.apply_contradictions(findings, reqs, checkout, submission_id="s", readme=README)
    assert out[0].state == "VERIFIED" and note is None


# --- the silence that started this file --------------------------------------------


def test_a_failed_pass_says_so_instead_of_looking_like_nothing_to_report(monkeypatch, setup):
    findings, reqs, checkout = setup
    fake_agent(monkeypatch, raises=RuntimeError("model unavailable"))
    out, _m, note = C.apply_contradictions(findings, reqs, checkout, submission_id="s", readme=README)
    assert out[0].state == "VERIFIED"      # nothing is invented on failure
    assert note and "did not run" in note  # but the run records that it did not happen


def test_a_submission_with_no_claims_is_reported_as_such(setup):
    findings, reqs, checkout = setup
    bare = Checkout(root=REPO, commitSha=SHA, pages={})
    out, _m, note = C.apply_contradictions(findings, reqs, bare, submission_id="s", readme="")
    assert note and "no claims" in note


def test_the_submissions_own_text_is_wrapped_as_untrusted(setup):
    """The README is still hostile input in this pass — it is quoted evidence, not instruction."""
    _f, _r, checkout = setup
    claims = C._claims_text(README, checkout.pages)
    assert '<untrusted source="README">' in claims
    assert '<untrusted source="report:p9">' in claims
