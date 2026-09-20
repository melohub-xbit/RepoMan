"""Stage 03: the evaluator's prose becomes a checklist they approve before anything runs.

This is where the thesis becomes visible on screen. A rubric line like "creativity and
originality — 15 marks" compiles to `verifiable: false` with a reason, and the UI renders it as
"Stays with you". RepoMan says out loud what it cannot check, and hands that part back.

The rubric is the *evaluator's* text, so unlike everything in `verify/verify.py` it is trusted
input and goes in the user turn directly. No submission content is anywhere near this call.
"""

from __future__ import annotations

import re

from strands import Agent

from repoman.core.resolver import normalise
from repoman.core.types import CompiledRubric, Requirement, Rubric, SourceSpan, new_id
from repoman.verify.model import make_model, model_id

COMPILE_SYSTEM = """\
You compile an evaluator's grading rubric into discrete, checkable requirements for RepoMan, a \
tool that gathers evidence about student and hackathon software submissions.

RepoMan can inspect exactly these things about a submission:
- the source repository at a pinned commit: files, code, configuration, dependency manifests, tests
- the git history: commit timeline and per-author contribution shape
- a submitted report or slide deck, as text, page by page
- whether a submitted deployment URL responds, its status and page title

RepoMan cannot run the code, execute tests, watch a video, judge visual design, assess novelty, \
or form an opinion about how impressive something is.

Rules:
1. Every requirement gets `verifiable: true` only if evidence for it could be *located* in the \
artifacts listed above. Otherwise set `verifiable: false` and give a one-sentence \
`unverifiableReason` naming what a human would have to look at instead.
2. `statement` is one sentence describing what must be true, written so that a reader could go \
looking for it. "JWT is issued on login and validated on protected routes", not "Authentication".
3. `weight` is the marks available. If the rubric gives marks, use them. If it does not, use 10.
4. `scale`: use "check" for pass/fail criteria, "levels" when the rubric describes bands (each \
level needs a label and points), otherwise "points".
5. If one rubric line bundles several separable things ("backend quality — 40 marks"), split it \
and set `proposedBy: "repoman"` on each piece so the evaluator can see it was decomposed. A line \
you did not split keeps `proposedBy: "evaluator"`.
6. `sourceQuote` must be copied verbatim from the rubric text — the exact words the requirement \
came from, so the evaluator can trace it back. Never paraphrase it.
7. Do not invent requirements the rubric does not contain, and do not drop ones it does.
8. Set `gate: true` only on a "check"-scale requirement that reads as a hard eligibility bar rather than a graded criterion — the rubric says "must", "required", "only considered if", "to be eligible", or names a specific mandatory technology ("must use AWS Bedrock", "must be built during the event", "submission must compile"). Most rubric lines are not gates: a gate marks a pre-filter a judge would apply before reading anything, not a point-scoring line. When in doubt, leave `gate` false — the evaluator can turn one on later with one click, and a wrongly-gated line hides nothing (docs/03) but still sorts a real submission out of the "read this first" order.

You never score anything. You are producing the checklist a human will approve and then grade with.\
"""


def compile_rubric(source_text: str, artifact_kinds: list[str] | None = None) -> Rubric:
    """Prose in, `Requirement[]` out. The evaluator edits and approves before any submission runs."""
    source_text = (source_text or "").strip()
    if not source_text:
        raise ValueError("nothing to compile: the rubric text is empty")

    available = ", ".join(artifact_kinds or ["repo"])
    agent = Agent(model=make_model(), system_prompt=COMPILE_SYSTEM, callback_handler=None)
    drafts = agent(
        f"The evaluator has submitted these artifacts for each submission: {available}.\n\n"
        f"Compile this rubric:\n\n{source_text}",
        structured_output_model=CompiledRubric,
    ).structured_output
    if drafts is None:
        raise ValueError("the model returned no compiled rubric")

    rubric = Rubric(sourceText=source_text, compiledBy=model_id())
    for draft in drafts.requirements:
        rubric.requirements.append(Requirement(
            id=new_id(), rubricId=rubric.id, title=draft.title.strip(),
            statement=draft.statement.strip(), weight=_weight_of(draft),
            scale=draft.scale, sourceSpan=locate(source_text, draft.sourceQuote),
            verifiable=draft.verifiable,
            unverifiableReason=(draft.unverifiableReason or None) if not draft.verifiable else None,
            proposedBy=draft.proposedBy,
            # A gate must be a real check (docs/03); untrusted model output is clamped, never trusted
            # to satisfy the invariant itself — the Requirement validator would reject it outright.
            gate=bool(draft.gate and draft.verifiable and draft.scale.kind == "check"),
        ))
    return rubric


def _weight_of(draft) -> float:
    """A levels scale's weight is its top band, whatever the model put in `weight`."""
    if draft.scale.kind == "levels" and draft.scale.levels:
        return max(level.points for level in draft.scale.levels)
    return float(draft.weight)


def locate(source_text: str, quote: str) -> SourceSpan | None:
    """Find `quote` in `source_text` and return real offsets, or None.

    Same rule as the locator resolver: the model is asked what it saw, not where it was. An
    offset a model counted itself is wrong often enough to make the rubric's provenance a lie,
    so a quote that cannot be found yields no span rather than a fabricated one.
    """
    quote = (quote or "").strip()
    if not quote:
        return None

    at = source_text.find(quote)
    if at >= 0:
        return SourceSpan(startChar=at, endChar=at + len(quote))

    # Whitespace-insensitive second pass: the model reflowed the line but the words are the source's.
    target = normalise(quote)
    if not target:
        return None
    pattern = re.compile(r"\s+".join(re.escape(word) for word in target.split(" ")), re.I)
    match = pattern.search(source_text)
    return SourceSpan(startChar=match.start(), endChar=match.end()) if match else None
