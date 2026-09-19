"""The verify pass, without a model.

Every rule in docs/04 that protects the evaluator lives on the path between what a model returns
and what gets stored. These tests drive that path with hand-written drafts, so they run offline
and fail loudly if someone relaxes a rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repoman.core.resolver import Checkout
from repoman.core.types import FileRange, FindingDraft, LocatorDraft, Requirement, Scale
from repoman.verify import verify as V
from repoman.verify.tools import ToolBox, untrusted

SHA = "9f3c2a1d7e4b8c0a5d6e7f8091a2b3c4d5e6f708"
REPO = Path(__file__).resolve().parent.parent / "fixtures" / "sample_run" / "repo"
INJECTED = Path(__file__).resolve().parent.parent / "fixtures" / "injected"


@pytest.fixture
def checkout() -> Checkout:
    return Checkout(root=REPO, commitSha=SHA, pages={"9": "full RBAC across all five user tiers"})


def requirement(**kw) -> Requirement:
    base = dict(id="r1", rubricId="rb", title="Role-based authorization",
                statement="Distinct roles are defined and enforced.", weight=20.0,
                scale=Scale(kind="points"), verifiable=True)
    return Requirement(**{**base, **kw})


def draft(state="VERIFIED", quote="ADMIN,\n    USER", path="src/main/java/app/model/UserRole.java",
          start=1, end=12, **kw) -> FindingDraft:
    base = dict(
        state=state, summary="Two roles are defined.", confidence="high",
        confidenceReason="Read the enum directly.",
        evidence=[LocatorDraft(locator=FileRange(path=path, startLine=start, endLine=end, commitSha="x"),
                               quote=quote)],
    )
    return FindingDraft(**{**base, **kw})


def run(monkeypatch, checkout, the_draft, *, req=None, **kw):
    """Drive verify_requirement with a fixed draft instead of a model."""
    monkeypatch.setattr(V, "_ask", lambda *a, **k: (the_draft, None))
    return V.verify_requirement(req or requirement(), checkout, submission_id="s", **kw)


# --- the happy path --------------------------------------------------------------


def test_a_resolving_citation_becomes_a_finding(monkeypatch, checkout):
    finding, _usage, dropped = run(monkeypatch, checkout, draft())
    assert finding.state == "VERIFIED" and dropped == 0
    assert finding.evidence[0].provenance == "model"
    assert finding.evidence[0].locator.commitSha == SHA


# --- Rule 2: citations that do not resolve ----------------------------------------


def test_a_bluffed_citation_is_dropped_and_downgrades_the_state(monkeypatch, checkout):
    """The model claims VERIFIED but half its evidence is invented."""
    d = draft(evidence=[
        LocatorDraft(locator=FileRange(path="src/main/java/app/model/UserRole.java", startLine=1,
                                       endLine=12, commitSha="x"), quote="ADMIN,\n    USER"),
        LocatorDraft(locator=FileRange(path="src/main/java/app/model/UserRole.java", startLine=1,
                                       endLine=12, commitSha="x"), quote="AUDITOR, OWNER, SUPERADMIN"),
    ])
    finding, _u, dropped = run(monkeypatch, checkout, d)
    assert dropped == 1
    assert finding.state == "PARTIAL"  # not VERIFIED: some of what it claimed was not there
    assert len(finding.evidence) == 1
    assert "did not resolve" in finding.confidenceReason


def test_a_finding_whose_every_citation_fails_becomes_unverified(monkeypatch, checkout):
    d = draft(quote="this text is nowhere in the repository")
    finding, _u, dropped = run(monkeypatch, checkout, d)
    assert finding.state == "UNVERIFIED" and dropped == 1
    assert finding.confidence == "low"
    assert finding.evidence, "invariant 2: a finding still needs a locator"


# --- invariant 4: 'we looked and found nothing' is a real answer --------------------


def test_a_model_failure_becomes_an_unverified_finding_not_a_crash(monkeypatch, checkout):
    def boom(*a, **k):
        raise RuntimeError("bedrock exploded")

    monkeypatch.setattr(V, "_ask", boom)
    finding, _u, _d = V.verify_requirement(requirement(), checkout, submission_id="s")
    assert finding.state == "UNVERIFIED"
    assert "RuntimeError" in finding.confidenceReason
    assert finding.evidence  # still cites something: the search itself


def test_an_unverified_finding_carries_questions_for_the_evaluator(monkeypatch, checkout):
    finding, _u, _d = run(monkeypatch, checkout, draft(quote="nowhere at all"))
    assert finding.questions


def test_a_verified_finding_does_not_carry_viva_questions(monkeypatch, checkout):
    finding, _u, _d = run(monkeypatch, checkout, draft(questions=["why?"]))
    assert finding.state == "VERIFIED" and finding.questions == []


# --- flags and exhaustion ----------------------------------------------------------


def test_submission_flags_land_on_every_finding(monkeypatch, checkout):
    finding, _u, _d = run(monkeypatch, checkout, draft(), flags=["PROMPT_INJECTION"])
    assert finding.flagged == ["PROMPT_INJECTION"]


def test_unverifiable_requirements_are_never_sent_to_a_model(monkeypatch, checkout):
    called = []
    monkeypatch.setattr(V, "_ask", lambda *a, **k: called.append(1) or (draft(), None))
    out = V.verify_all([requirement(id="r1", verifiable=False, unverifiableReason="human judgement")],
                       checkout, submission_id="s")
    assert called == [] and out.findings == []


# --- the tools ---------------------------------------------------------------------


def test_every_tool_result_is_wrapped_as_untrusted():
    box = ToolBox(root=REPO)
    fns = {t.tool_name: t._tool_func for t in box.build()}
    for name in ("tree", "grep", "read_file", "read_report_page", "list_deps"):
        result = fns[name](".") if name == "tree" else (
            fns[name]("class", "*.java") if name == "grep" else
            fns[name]("pom.xml") if name == "read_file" else
            fns[name](1) if name == "read_report_page" else fns[name]())
        assert "<untrusted" in result, name


def test_probe_results_is_not_wrapped_because_it_is_our_own_text():
    box = ToolBox(root=REPO, probe_summary="deps: one unused.")
    fns = {t.tool_name: t._tool_func for t in box.build()}
    assert "<untrusted" not in fns["probe_results"]()


def test_tools_refuse_to_read_outside_the_checkout():
    box = ToolBox(root=REPO)
    fns = {t.tool_name: t._tool_func for t in box.build()}
    out = fns["read_file"]("../../../AGENTS.md")
    assert "not found" in out and "invariant" not in out.lower()


def test_quarantined_files_are_withheld_from_the_agent():
    box = ToolBox(root=INJECTED, quarantined=frozenset({"README.md"}))
    fns = {t.tool_name: t._tool_func for t in box.build()}
    assert "withheld" in fns["read_file"]("README.md")
    assert "README.md" not in fns["tree"](".")
    assert "award full marks" not in fns["grep"]("marks")


def test_the_tool_budget_is_enforced_in_code_and_reported():
    box = ToolBox(root=REPO, cap=2)
    fns = {t.tool_name: t._tool_func for t in box.build()}
    fns["tree"]("."), fns["tree"](".")
    assert box.exhausted
    assert "budget spent" in fns["tree"](".")


def test_read_file_never_returns_more_than_its_cap():
    box = ToolBox(root=REPO)
    fns = {t.tool_name: t._tool_func for t in box.build()}
    out = fns["read_file"]("pom.xml", 1, 100000)
    body = [line for line in out.splitlines() if line[:1].isdigit()]
    assert len(body) <= 200


def test_untrusted_wrapper_names_its_source():
    assert untrusted("file:a.java", "x").startswith('<untrusted source="file:a.java">')


# --- tolerating what models actually send -----------------------------------------
# A real qwen2.5 run sent every locator without a `kind` discriminator, which failed validation
# and pushed five good findings to UNVERIFIED. The bookkeeping fields are inferable; losing a real
# citation over a missing tag is not acceptable.


def test_a_locator_without_its_discriminator_is_still_understood():
    d = LocatorDraft.model_validate(
        {"locator": {"path": "a.java", "startLine": 4, "endLine": 6, "commitSha": "latest"}, "quote": "x"})
    assert d.locator.kind == "file_range"


@pytest.mark.parametrize("loc,expected", [
    ({"page": 9}, "doc_span"),
    ({"url": "https://x.app", "status": 200, "capturedAt": "2026-09-18T00:00:00Z"}, "http_capture"),
    ({"commitSha": "a" * 40, "authorHash": "abc", "committedAt": "2026-09-18T00:00:00Z"}, "git_object"),
    ({"path": "a.java"}, "file_range"),
])
def test_locator_kind_is_inferred_from_the_fields_present(loc, expected):
    assert LocatorDraft.model_validate({"locator": loc, "quote": "x"}).locator.kind == expected


def test_a_finding_draft_parses_without_any_bookkeeping_fields():
    f = FindingDraft.model_validate({
        "state": "VERIFIED", "summary": "s", "confidence": "high", "confidenceReason": "r",
        "evidence": [{"locator": {"path": "a.java", "startLine": 1, "endLine": 2}, "quote": "q"}]})
    assert f.evidence[0].locator.commitSha == ""  # the resolver supplies the real one


def test_tolerance_does_not_extend_to_inventing_evidence():
    """Bookkeeping is filled in; evidence is not. A finding with no citation still fails (Rule 1)."""
    with pytest.raises(Exception):
        FindingDraft.model_validate({"state": "VERIFIED", "summary": "s", "confidence": "high",
                                     "confidenceReason": "r", "evidence": []})
