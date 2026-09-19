"""Run every probe over one acquired submission. Stage 04.

The output of this module is `runs/<id>/probes.json`, plus two things the later stages need:
the set of flags every finding for this submission will carry, and the set of artifacts that
injection quarantined — which the agent's tools then refuse to read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from repoman.core.types import Evidence, FlagKind, Submission
from repoman.probes import ProbeResult, deploy, deps, fork, git, injection, similarity, tests


@dataclass
class ProbeReport:
    results: dict[str, ProbeResult] = field(default_factory=dict)
    flags: list[FlagKind] = field(default_factory=list)
    quarantined: set[str] = field(default_factory=set)
    # This submission's file hashes. Cross-submission similarity is a batch-level comparison made
    # later from these, because its answer changes as the batch fills (see probes/similarity.py).
    fingerprint: dict[str, str] = field(default_factory=dict)

    @property
    def captures(self) -> tuple[Evidence, ...]:
        """Probe evidence a model is allowed to cite: the ones it could not have invented."""
        return tuple(ev for r in self.results.values() for ev in r.evidence
                     if ev.locator.kind in ("http_capture", "git_object"))

    def summary_for_prompt(self) -> str:
        """The `<hints>` block of the verify user turn. Probe summaries are our own text, not the
        submission's, so this is the one place submission-derived facts reach the model as a user
        turn — as our sentences about it, never as its own bytes."""
        return "\n".join(f"{name}: {r.summary}" for name, r in sorted(self.results.items()) if r.summary)

    def as_json(self) -> dict:
        return {name: r.model_dump() for name, r in self.results.items()}


def run_probes(repo: Path, sub: Submission, *, pages: dict[str, str] | None = None,
               event_window: tuple[str, str] | None = None, client=None) -> ProbeReport:
    report = ProbeReport()

    report.results["deps"] = deps.probe(repo, sub)
    report.results["tests"] = tests.probe(repo, sub)
    git_result = git.probe(repo, sub, event_window)
    report.results["git"] = git_result

    report.results["fork"] = fork.probe(repo, sub, git_result.data, client=client)

    subjects = [ev.quote for ev in git_result.evidence if ev.locator.kind == "git_object"]
    report.results["injection"] = injection.probe(repo, sub, pages=pages, commit_subjects=subjects)
    report.results["deploy"] = deploy.probe(sub, client=client)
    report.fingerprint = similarity.fingerprint(repo)

    seen: list[FlagKind] = []
    for result in report.results.values():
        for flag in result.flags:
            if flag not in seen:
                seen.append(flag)
    report.flags = seen

    # An artifact that carried a payload is withheld from the agent entirely (docs/05 threat 1,
    # step 2). Detection is defence in depth; this exclusion is what it buys.
    inj = report.results["injection"]
    report.quarantined = {ev.locator.path for ev in inj.evidence if ev.locator.kind == "file_range"}
    if any(ev.locator.kind == "doc_span" for ev in inj.evidence):
        report.quarantined.add("report")  # the whole PDF: a payload on one page poisons the document
    for artifact in sub.artifacts:
        if artifact.uri in report.quarantined or (artifact.kind == "report" and "report" in report.quarantined):
            artifact.quarantined = True

    return report
