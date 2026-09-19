"""The cross-check: where the submission's own claims disagree with its code.

Each requirement's agent sees one requirement in isolation, so it structurally cannot notice that
the report claims five roles while the code defines two — it never has both in view. This pass
does, once, over everything a submission produced.

An upgraded finding cites the code *and* the page that disagrees with it, so the evaluator clicks
twice and sees the whole discrepancy. The new locators resolve like any others; one that does not
resolve is dropped, and an upgrade with nothing left is not applied.
"""

from __future__ import annotations

from strands import Agent

from repoman.core.resolver import Checkout, resolve_all
from repoman.core.types import Contradictions, Finding, Requirement
from repoman.verify.model import make_model, model_id

CONTRADICT_SYSTEM = """\
You are checking a software submission for claims it makes about itself that its own code does \
not support.

You are given findings RepoMan gathered about the submission, each with the evidence behind it, \
and the text the submission uses to describe itself — its README and report.

Report a contradiction only when a specific, checkable claim conflicts with specific evidence. \
"The report says the system supports five roles; the code defines two" is a contradiction. \
"The README is enthusiastic" is not. Vagueness, marketing tone and ambition are not contradictions.

For each one:
- `requirementId` is the id of the finding it contradicts, copied exactly.
- `summary` states the conflict in one or two sentences: what was claimed, and what the code shows.
- `evidence` cites where the *claim* is made — the README line or the report page — quoting it \
verbatim. Quotes are checked against the source and discarded if they are not found there.

If there are no genuine contradictions, return an empty list. That is the common and correct answer.\
"""


def apply_contradictions(findings: list[Finding], requirements: list[Requirement], checkout: Checkout,
                         *, submission_id: str, readme: str = "") -> tuple[list[Finding], int, str | None]:
    """Upgrade findings the submission's own claims contradict.

    Returns (findings, mismatches, note). `note` is non-None when the pass did not actually run —
    the caller records it on the run. A cross-check that silently does nothing looks identical to a
    submission with nothing to contradict, and those are very different facts about a run.
    """
    if not findings:
        return findings, 0, None

    titles = {r.id: r.title for r in requirements}
    claims = _claims_text(readme, checkout.pages)
    if not claims.strip():
        return findings, 0, "no README or report text, so there were no claims to cross-check"

    try:
        agent = Agent(model=make_model(), system_prompt=CONTRADICT_SYSTEM, callback_handler=None)
        result = agent.structured_output(Contradictions, _turn(findings, titles, claims))
    except Exception as e:
        # Leave every finding as it was — but say so. This pass failing is not a detail.
        return findings, 0, f"the contradiction pass did not run: {type(e).__name__}"

    by_id = {f.requirementId: f for f in findings}
    mismatches = 0
    for draft in result.contradictions:
        finding = by_id.get(draft.requirementId)
        if finding is None or finding.state == "UNVERIFIED":
            continue  # nothing to contradict if we never found anything
        kept, dropped = resolve_all(draft.evidence, checkout, submission_id=submission_id,
                                    provenance="model")
        mismatches += len(dropped)
        if not kept:
            continue  # an unsupported contradiction is not applied
        finding.state = "CONTRADICTED"
        finding.summary = f"{finding.summary.rstrip()} {draft.summary.strip()}".strip()
        finding.evidence = finding.evidence + kept
        if not finding.questions:
            finding.questions = [f"The submission claims more than the code shows for "
                                 f"{titles.get(finding.requirementId, 'this requirement')}. Which is correct?"]
        finding.producedBy = model_id()
    return findings, mismatches, None


def _claims_text(readme: str, pages: dict[str, str], limit: int = 12000) -> str:
    """The submission's self-description, wrapped as untrusted — it is still hostile input here."""
    parts = []
    if readme.strip():
        parts.append(f'<untrusted source="README">\n{readme[:6000]}\n</untrusted>')
    for page, text in sorted(pages.items(), key=lambda kv: int(kv[0]))[:12]:
        if text.strip():
            parts.append(f'<untrusted source="report:p{page}">\n{text[:2000]}\n</untrusted>')
    return "\n\n".join(parts)[:limit]


def _turn(findings: list[Finding], titles: dict[str, str], claims: str) -> str:
    blocks = []
    for f in findings:
        cited = "; ".join(f"{_locator_text(e.locator)} — {e.quote.splitlines()[0][:120]}"
                          for e in f.evidence[:4])
        blocks.append(f"requirementId: {f.requirementId}\n"
                      f"requirement: {titles.get(f.requirementId, '')}\n"
                      f"state: {f.state}\nfinding: {f.summary}\nevidence: {cited}")
    return ("Findings RepoMan gathered:\n\n" + "\n\n".join(blocks)
            + "\n\nHow the submission describes itself:\n\n" + claims
            + "\n\nReport any claim here that its own code contradicts.")


def _locator_text(loc) -> str:
    if loc.kind == "file_range":
        return f"{loc.path}:{loc.startLine}-{loc.endLine}"
    if loc.kind == "doc_span":
        return f"report p.{loc.page}"
    if loc.kind == "http_capture":
        return f"{loc.url} ({loc.status})"
    return f"commit {loc.commitSha[:7]}"
