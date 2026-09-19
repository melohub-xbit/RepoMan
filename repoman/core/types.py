"""Domain types. Source of truth is docs/03-data-model.md — change that first, then this.

No I/O here. Nothing outside the stdlib and pydantic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid4().hex[:12]


# --- EvidenceLocator: the spine -------------------------------------------------


class FileRange(BaseModel):
    kind: Literal["file_range"] = "file_range"
    path: str
    startLine: int
    endLine: int
    commitSha: str


class DocSpan(BaseModel):
    kind: Literal["doc_span"] = "doc_span"
    artifactId: str
    page: int  # 1-indexed


class HttpCapture(BaseModel):
    kind: Literal["http_capture"] = "http_capture"
    url: str
    status: int
    capturedAt: str
    title: str | None = None


class GitObject(BaseModel):
    kind: Literal["git_object"] = "git_object"
    commitSha: str
    authorHash: str
    committedAt: str


EvidenceLocator = Annotated[FileRange | DocSpan | HttpCapture | GitObject, Field(discriminator="kind")]


# --- Submission ----------------------------------------------------------------

ArtifactKind = Literal["repo", "readme", "report", "deploy"]


class Artifact(BaseModel):
    id: str = Field(default_factory=new_id)
    submissionId: str
    kind: ArtifactKind
    uri: str
    sha256: str | None = None
    quarantined: bool = False


class SubmissionIdentity(BaseModel):
    team: str | None = None
    members: list[str] = []
    institution: str | None = None


class Submission(BaseModel):
    id: str = Field(default_factory=new_id)
    batchId: str
    source: Literal["github", "zip"]
    repoUrl: str | None = None
    commitSha: str
    artifacts: list[Artifact] = []
    acquiredAt: str = Field(default_factory=now)
    identity: SubmissionIdentity | None = None


# --- Rubric --------------------------------------------------------------------


class SourceSpan(BaseModel):
    startChar: int
    endChar: int


class Requirement(BaseModel):
    id: str = Field(default_factory=new_id)
    rubricId: str
    title: str
    statement: str
    weight: float
    sourceSpan: SourceSpan
    verifiable: bool
    unverifiableReason: str | None = None
    proposedBy: Literal["evaluator", "repoman"] = "evaluator"


class Rubric(BaseModel):
    id: str = Field(default_factory=new_id)
    version: int = 1
    sourceText: str
    requirements: list[Requirement] = []
    compiledBy: str


# --- Evidence and findings -----------------------------------------------------


class Evidence(BaseModel):
    id: str = Field(default_factory=new_id)
    submissionId: str
    locator: EvidenceLocator
    quote: str  # re-read from the source by the resolver, never echoed from the model
    provenance: Literal["probe", "model"]
    probeId: str | None = None
    resolvedAt: str = Field(default_factory=now)


FindingState = Literal["VERIFIED", "PARTIAL", "UNVERIFIED", "CONTRADICTED"]
FlagKind = Literal[
    "PROMPT_INJECTION", "TIMELINE_ANOMALY", "COHORT_SIMILARITY", "FORK_SUSPECTED", "CONTRIBUTION_SKEW"
]
Confidence = Literal["high", "medium", "low"]


class Finding(BaseModel):
    id: str = Field(default_factory=new_id)
    submissionId: str
    requirementId: str
    state: FindingState
    flagged: list[FlagKind] = []
    summary: str
    evidence: list[Evidence] = Field(min_length=1)  # no locator, no finding — enforced here
    confidence: Confidence
    confidenceReason: str
    searchExhausted: bool = False
    questions: list[str] = []
    producedBy: str


# --- What the model is allowed to return (verify/) -----------------------------


class LocatorDraft(BaseModel):
    """A proposed citation. Becomes Evidence only if the resolver finds `quote` there."""

    locator: EvidenceLocator
    quote: str


class FindingDraft(BaseModel):
    state: FindingState
    summary: str
    evidence: list[LocatorDraft] = Field(min_length=1)
    confidence: Confidence
    confidenceReason: str
    questions: list[str] = []


class RequirementDraft(BaseModel):
    title: str
    statement: str
    weight: float
    sourceSpan: SourceSpan
    verifiable: bool
    unverifiableReason: str | None = None
    proposedBy: Literal["evaluator", "repoman"] = "evaluator"


class CompiledRubric(BaseModel):
    requirements: list[RequirementDraft] = Field(min_length=1)


# --- The human's contribution --------------------------------------------------


class Decision(BaseModel):
    id: str = Field(default_factory=new_id)
    submissionId: str
    requirementId: str
    evaluatorId: str
    score: float | None = None  # the human's number. RepoMan never writes here.
    note: str = ""
    overrodeFindingId: str | None = None
    decidedAt: str = Field(default_factory=now)


class Precedent(BaseModel):
    id: str = Field(default_factory=new_id)
    batchId: str
    requirementId: str
    rule: str
    derivedFromDecisionId: str
    appliesFrom: str = Field(default_factory=now)


# --- Run bookkeeping -----------------------------------------------------------


class UsageRecord(BaseModel):
    pass_: str = Field(alias="pass")
    inputTokens: int = 0
    outputTokens: int = 0
    cacheReadTokens: int = 0
    cacheWriteTokens: int = 0

    model_config = {"populate_by_name": True}


class RunManifest(BaseModel):
    id: str = Field(default_factory=new_id)
    submissionId: str
    rubricId: str
    rubricVersion: int
    commitSha: str
    artifactShas: dict[str, str] = {}
    probeVersions: dict[str, str] = {}
    modelId: str
    promptHashes: dict[str, str] = {}
    startedAt: str = Field(default_factory=now)
    finishedAt: str | None = None
    usage: list[UsageRecord] = []
    mismatches: int = 0


RunStage = Literal["queued", "acquire", "probe", "verify", "contradict", "done", "failed"]


class RunStatus(BaseModel):
    stage: RunStage
    detail: str = ""
    done: int = 0
    total: int = 0
    updatedAt: str = Field(default_factory=now)


class Batch(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    eventWindow: tuple[str, str] | None = None
    rubricId: str | None = None
    runIds: list[str] = []
    createdAt: str = Field(default_factory=now)


def coverage(findings: list[Finding], requirements: list[Requirement]) -> tuple[int, int]:
    """The one number RepoMan produces: verified count over verifiable count. A count, not a score."""
    verifiable = {r.id for r in requirements if r.verifiable}
    verified = {f.requirementId for f in findings if f.state == "VERIFIED" and f.requirementId in verifiable}
    return len(verified), len(verifiable)


if __name__ == "__main__":
    # self-check: the schema rejects a finding with no evidence
    from pydantic import ValidationError

    try:
        Finding(submissionId="s", requirementId="r", state="VERIFIED", summary="x", evidence=[],
                confidence="high", confidenceReason="", producedBy="test")
        raise SystemExit("schema accepted an evidence-less finding")
    except ValidationError:
        pass
    ev = Evidence(submissionId="s", locator=FileRange(path="a.py", startLine=1, endLine=2, commitSha="abc"),
                  quote="x", provenance="probe")
    f = Finding(submissionId="s", requirementId="r", state="VERIFIED", summary="x", evidence=[ev],
                confidence="high", confidenceReason="", producedBy="test")
    assert Finding.model_validate_json(f.model_dump_json()).evidence[0].locator.kind == "file_range"
    print("types ok")
