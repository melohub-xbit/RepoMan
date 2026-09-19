"""The rubric compiler.

The offline tests cover `locate()`, which decides whether a requirement can be traced back to the
evaluator's own words. The live test is docs/04 Rule 5 — "creativity and originality" must compile
to an unverifiable requirement — and is skipped unless a model is configured.
"""

from __future__ import annotations

import os

import pytest

from repoman.core.types import CompiledRubric, RequirementDraft, Scale
from repoman.verify import compile as C

RUBRIC = """\
1. **Authentication — 20 marks.** The application issues a JWT on login.
2. **Creativity and originality — 15 marks.** How imaginative the project is.
"""

needs_model = pytest.mark.skipif(
    not (os.environ.get("REPOMAN_OLLAMA_HOST") or os.environ.get("REPOMAN_MODEL_ID")),
    reason="no model configured; set REPOMAN_OLLAMA_HOST or REPOMAN_MODEL_ID",
)


# --- locate(): provenance back into the evaluator's text --------------------------


def test_an_exact_quote_gets_real_offsets():
    span = C.locate(RUBRIC, "The application issues a JWT on login.")
    assert span is not None
    assert RUBRIC[span.startChar:span.endChar] == "The application issues a JWT on login."


def test_a_reflowed_quote_still_locates():
    """Models re-wrap lines. The words are the source's, so the span is real."""
    span = C.locate(RUBRIC, "The application   issues a JWT\non login.")
    assert span is not None
    assert "issues a JWT on login" in RUBRIC[span.startChar:span.endChar]


def test_a_paraphrase_yields_no_span_rather_than_a_wrong_one():
    assert C.locate(RUBRIC, "the app should authenticate users somehow") is None


def test_an_empty_quote_yields_no_span():
    assert C.locate(RUBRIC, "") is None
    assert C.locate(RUBRIC, "   ") is None


# --- assembling the rubric --------------------------------------------------------


def _compiled(monkeypatch, drafts):
    monkeypatch.setattr(C, "make_model", lambda: object())
    monkeypatch.setattr(C.Agent, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(C.Agent, "structured_output",
                        lambda self, model, prompt: CompiledRubric(requirements=drafts))
    return C.compile_rubric(RUBRIC, ["repo"])


def test_an_unverifiable_requirement_keeps_its_reason(monkeypatch):
    rubric = _compiled(monkeypatch, [RequirementDraft(
        title="Creativity", statement="How imaginative it is.", weight=15, verifiable=False,
        unverifiableReason="Nothing in a repository shows imagination.",
        sourceQuote="How imaginative the project is.")])
    req = rubric.requirements[0]
    assert req.verifiable is False
    assert req.unverifiableReason
    assert req.sourceSpan is not None  # traceable to the evaluator's line


def test_a_verifiable_requirement_carries_no_unverifiable_reason(monkeypatch):
    rubric = _compiled(monkeypatch, [RequirementDraft(
        title="Auth", statement="A JWT is issued on login.", weight=20, verifiable=True,
        unverifiableReason="leftover text from the model", sourceQuote="issues a JWT on login")])
    assert rubric.requirements[0].unverifiableReason is None


def test_a_levels_scale_takes_its_weight_from_the_top_band(monkeypatch):
    scale = Scale(kind="levels", levels=[
        {"label": "none", "points": 0}, {"label": "some", "points": 10},
        {"label": "comprehensive", "points": 20}])
    rubric = _compiled(monkeypatch, [RequirementDraft(
        title="Tests", statement="Tests exist.", weight=999, scale=scale, verifiable=True,
        sourceQuote="")])
    assert rubric.requirements[0].weight == 20


def test_a_typed_in_requirement_has_no_source_span(monkeypatch):
    rubric = _compiled(monkeypatch, [RequirementDraft(
        title="Auth", statement="A JWT is issued.", weight=20, verifiable=True, sourceQuote="")])
    assert rubric.requirements[0].sourceSpan is None


def test_the_rubric_records_which_model_compiled_it(monkeypatch):
    rubric = _compiled(monkeypatch, [RequirementDraft(
        title="Auth", statement="x", weight=1, verifiable=True, sourceQuote="")])
    assert rubric.compiledBy and rubric.sourceText == RUBRIC.strip()


def test_empty_rubric_text_is_refused():
    with pytest.raises(ValueError):
        C.compile_rubric("   ", ["repo"])


# --- the live one -----------------------------------------------------------------


@needs_model
def test_creativity_compiles_to_an_unverifiable_requirement():
    """docs/04 Rule 5. If this regresses, the product is pretending to check taste."""
    rubric = C.compile_rubric(RUBRIC, ["repo", "readme", "report", "deploy"])
    creativity = next((r for r in rubric.requirements if "creativ" in r.title.lower()), None)
    assert creativity is not None, "the compiler dropped a rubric line"
    assert creativity.verifiable is False
    assert creativity.unverifiableReason
