"""One real investigation on whichever model the environment selects; prints what the manifest would record.

    AWS_REGION=eu-north-1 REPOMAN_MODEL_ID=eu.amazon.nova-2-lite-v1:0 uv run python scripts/bench_model.py
    AWS_REGION=eu-north-1 REPOMAN_MODEL_ID=eu.anthropic.claude-haiku-4-5-20251001-v1:0 uv run python scripts/bench_model.py

Same requirement, same fixture, same attached claim every time, so two models can be compared on the
only things that matter here: did the citations resolve, did it answer the claim, and what did it cost.
Bedrock API keys are read by boto3 from AWS_BEARER_TOKEN_BEDROCK; nothing here touches credentials.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repoman.core.resolver import Checkout  # noqa: E402
from repoman.core.types import Claim, Evidence, FileRange, Requirement  # noqa: E402
from repoman.probes import repomap  # noqa: E402
from repoman.verify import verify as V  # noqa: E402
from repoman.verify.model import model_id  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "sample_run" / "repo"
README_LINE = ("Task management API with JWT authentication, full role-based access control, Redis caching "
               "and a comprehensive test suite.")


def main() -> int:
    ck = Checkout(root=ROOT.resolve(), commitSha="bench", repo_map=repomap.build(ROOT.resolve()))
    req = Requirement(id="q2", rubricId="bench", title="Role-based authorization", weight=20, verifiable=True,
                      statement="Distinct user roles are defined and enforced on the endpoints that need them.")
    claim = Claim(id="c_rbac", submissionId="s", requirementId="q2",
                  statement="The API has full role-based access control on every endpoint.",
                  source=Evidence(submissionId="s", provenance="model", quote=README_LINE,
                                  locator=FileRange(path="README.md", startLine=3, endLine=3, commitSha="bench")))
    print(f"model: {model_id()}")
    t = time.time()
    finding, usage, dropped, answered = V.verify_requirement(req, ck, submission_id="s", claims=[claim])
    print(f"{time.time() - t:.0f}s | requirement: {finding.state} | citations kept {len(finding.evidence)}, "
          f"dropped {dropped} | exhausted: {finding.searchExhausted}")
    print(f"   {finding.summary[:200]}")
    print(f"   reason: {finding.confidenceReason[:200]}")
    for e in finding.evidence:
        loc = e.locator
        print(f"   - {getattr(loc, 'path', loc.kind)}:{getattr(loc, 'startLine', '')}–{getattr(loc, 'endLine', '')}  "
              f"{e.quote.splitlines()[0][:70]}")
    for cf in answered:
        print(f"claim: {cf.state} | {cf.summary[:200]}")
    if not answered:
        print("claim: (no verdict returned)")
    if usage:
        print(f"usage: {usage.inputTokens} in / {usage.outputTokens} out / cache read {usage.cacheReadTokens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
