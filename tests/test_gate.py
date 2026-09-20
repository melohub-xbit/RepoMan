"""Eligibility gates: a hard pre-filter that never hides, never scores, never blocks an override."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from repoman.core.types import (Batch, Evidence, FileRange, Finding, Requirement, Rubric, RunStatus,
                                Scale, Submission, gate_status)

SHA = "9f3c2a1d7e4b8c0a5d6e7f8091a2b3c4d5e6f708"


# --- the invariant itself -------------------------------------------------------


def test_gate_requires_a_checkable_check_scale():
    with pytest.raises(ValidationError):
        Requirement(rubricId="r", title="t", statement="s", weight=10, verifiable=True,
                    scale=Scale(kind="points"), gate=True)
    with pytest.raises(ValidationError):
        Requirement(rubricId="r", title="t", statement="s", weight=10, verifiable=False,
                    scale=Scale(kind="check"), gate=True)
    Requirement(rubricId="r", title="t", statement="s", weight=10, verifiable=True,
               scale=Scale(kind="check"), gate=True)  # the one shape that is allowed


# --- gate_status -----------------------------------------------------------------


def req(rid, gate=False, verifiable=True, kind="check"):
    return Requirement(id=rid, rubricId="r", title=rid, statement="s", weight=10, verifiable=verifiable,
                       scale=Scale(kind=kind), gate=gate)


def finding(rid, state, subject="requirement"):
    return Finding(submissionId="s", requirementId=rid, subject=subject, state=state, summary="x",
                   confidence="high", confidenceReason="x", producedBy="test",
                   evidence=[Evidence(submissionId="s", provenance="probe", quote="x",
                                      locator=FileRange(path="a", startLine=1, endLine=1, commitSha=SHA))])


def test_gate_status_reports_met_not_met_and_pending():
    reqs = [req("g1", gate=True), req("g2", gate=True), req("q1")]  # q1 is not a gate
    findings = [finding("g1", "VERIFIED"), finding("g2", "PARTIAL")]  # g2 not yet unresolved -> not met
    assert gate_status(findings, reqs) == {"g1": True, "g2": False}


def test_gate_status_is_none_when_no_finding_exists_yet():
    assert gate_status([], [req("g1", gate=True)]) == {"g1": None}


def test_gate_status_ignores_claim_findings():
    reqs = [req("g1", gate=True)]
    # a claim finding happens to reuse the requirement's id as its own key space in a different run;
    # gate_status must only look at subject=="requirement"
    findings = [finding("g1", "VERIFIED", subject="claim")]
    assert gate_status(findings, reqs) == {"g1": None}


def test_no_gates_is_an_empty_dict():
    assert gate_status([finding("q1", "VERIFIED")], [req("q1")]) == {}


# --- the compiler clamps untrusted output ----------------------------------------


def test_compile_clamps_a_gate_on_the_wrong_scale(monkeypatch):
    from types import SimpleNamespace

    from repoman.core.types import CompiledRubric, RequirementDraft
    from repoman.verify import compile as C

    draft = RequirementDraft(title="Uses Bedrock", statement="Calls a Bedrock model.", weight=10,
                             scale=Scale(kind="points"), verifiable=True, gate=True,  # untrusted: wrong scale
                             sourceQuote="")
    monkeypatch.setattr(C, "make_model", lambda: object())
    monkeypatch.setattr(C.Agent, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(C.Agent, "__call__",
                        lambda self, prompt, **kw: SimpleNamespace(structured_output=CompiledRubric(requirements=[draft])))
    rubric = C.compile_rubric("1. Uses AWS Bedrock.", ["repo"])
    assert rubric.requirements[0].gate is False  # clamped, not a crash


# --- the web workspace -------------------------------------------------------------


@pytest.fixture
def gate_client(tmp_path):
    from repoman.store import LocalStore
    from repoman.web import app as web

    store = LocalStore(tmp_path)
    web.store = store
    rubric = Rubric(id="rb", sourceText="1. Uses AWS Bedrock.\n2. Automated tests.", compiledBy="test", requirements=[
        Requirement(id="g1", rubricId="rb", title="Uses AWS Bedrock", statement="Calls a Bedrock model.",
                    weight=10.0, scale=Scale(kind="check"), verifiable=True, gate=True),
        Requirement(id="q1", rubricId="rb", title="Automated tests", statement="Tests exist.", weight=20.0,
                    scale=Scale(kind="points"), verifiable=True),
    ])
    store.put_json("rubrics/rb.json", rubric)
    store.put_json("batches/b1.json", Batch(id="b1", name="Hackathon", rubricId="rb"))

    def make_run(run_id, gate_state):
        store.put_json(f"runs/{run_id}/submission.json", Submission(id=run_id, batchId="b1", source="github",
                                                                     repoUrl=f"https://github.com/team/{run_id}", commitSha=SHA))
        store.put_json(f"runs/{run_id}/findings.json", [finding("g1", gate_state), finding("q1", "VERIFIED")])
        store.put_json(f"runs/{run_id}/decisions.json", [])
        store.put_json(f"runs/{run_id}/status.json", RunStatus(stage="done", done=2, total=2))
        store.put_json(f"runs/{run_id}/probes.json", {})

    make_run("eligible1", "VERIFIED")
    make_run("notelig1", "UNVERIFIED")
    batch = Batch.model_validate(store.get_json("batches/b1.json"))
    batch.runIds = ["notelig1", "eligible1"]  # deliberately out of eligibility order
    store.put_json("batches/b1.json", batch)
    return TestClient(web.app), store


def test_batch_page_shows_eligibility_chips_and_sorts_ineligible_last(gate_client):
    c, _ = gate_client
    page = c.get("/batches/b1").text
    assert 'class="gate-chip yes"' in page and 'class="gate-chip no"' in page
    assert page.index("eligible1") < page.index("notelig1")  # eligible sorts first despite runIds order
    assert "meet every eligibility gate" in page and "Gates: Uses AWS Bedrock" in page


def test_run_page_shows_the_gate_badge_on_the_gated_requirement(gate_client):
    c, _ = gate_client
    page = c.get("/runs/eligible1").text
    assert page.count('class="gate-badge"') == 1  # only on g1, not on q1


def test_csv_export_has_eligibility_and_gate_columns(gate_client):
    c, _ = gate_client
    csv_ = c.get("/batches/b1/export.csv").text
    header = csv_.splitlines()[0]
    assert "eligible" in header and "gate" in header
    assert ",yes,Uses AWS Bedrock,yes," in csv_.replace("eligible1", "").replace("https://github.com/team/", "")


def test_packet_names_the_gate_and_eligibility(gate_client):
    c, _ = gate_client
    packet = c.get("/runs/eligible1/packet.md").text
    assert "Meets every eligibility gate." in packet and "[GATE] Uses AWS Bedrock" in packet
    packet2 = c.get("/runs/notelig1/packet.md").text
    assert "Fails an eligibility gate." in packet2


def test_rubric_approve_saves_a_gate_and_clamps_an_invalid_one(gate_client):
    c, store = gate_client
    form = {
        "id": ["g1", "q1"],
        "title-g1": "Uses AWS Bedrock", "statement-g1": "Calls a Bedrock model.", "scale-g1": "check",
        "weight-g1": "10", "verifiable-g1": "on", "gate-g1": "on",
        "title-q1": "Automated tests", "statement-q1": "Tests exist.", "scale-q1": "points",
        "weight-q1": "20", "verifiable-q1": "on", "gate-q1": "on",  # invalid: points scale, must clamp
    }
    r = c.post("/batches/b1/rubric", data=form, follow_redirects=False)
    assert r.status_code == 303
    from repoman.core.types import Rubric as R
    saved = R.model_validate(store.get_json("rubrics/rb.json"))
    by_id = {req.id: req for req in saved.requirements}
    assert by_id["g1"].gate is True
    assert by_id["q1"].gate is False  # clamped: points scale can never be a gate


def test_eligible_only_filter_checkbox_is_present(gate_client):
    c, _ = gate_client
    page = c.get("/batches/b1").text
    assert 'id="eligible-only"' in page and "Show only eligible" in page
