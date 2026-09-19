"""Domain types. Source of truth is docs/03-data-model.md — change that first, then this.

No I/O here. Nothing outside the stdlib and pydantic.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid4().hex[:12]


def hash_author(identifier: str) -> str:
    """Author emails are hashed where the git log is read, before anything downstream sees them.

    docs/05 boundary 4: the strip happens at acquisition, not at render time — the bias it exists
    to prevent happens inside the model, so a name that reaches the prompt has already done its damage.
    """
    return hashlib.sha256(identifier.strip().lower().encode()).hexdigest()[:12]


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


class Level(BaseModel):
    label: str
    description: str = ""
    points: float


class Scale(BaseModel):
    """How the *human* scores a requirement. Never shown to a model; `statement` is what RepoMan checks."""

    kind: Literal["check", "points", "levels"] = "points"
    levels: list[Level] = []  # only when kind == "levels"

    @model_validator(mode="after")
    def _levels_present(self):
        if self.kind == "levels" and not self.levels:
            raise ValueError("a levels scale needs at least one level")
        return self


class Requirement(BaseModel):
    id: str = Field(default_factory=new_id)
    rubricId: str
    title: str
    statement: str
    weight: float  # max marks; for a levels scale, the highest level's points
    scale: Scale = Scale()
    sourceSpan: SourceSpan | None = None  # None when typed in directly rather than compiled from sourceText
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


class Claim(BaseModel):
    """Something the submission says about itself (README, report), in checkable form, with where it said it."""

    id: str = Field(default_factory=new_id)
    submissionId: str
    statement: str  # the checkable form, e.g. "Redis is used as a cache for read-heavy endpoints"
    source: Evidence  # the claim's own words at their location; resolved like any evidence


class Finding(BaseModel):
    id: str = Field(default_factory=new_id)
    submissionId: str
    requirementId: str  # a Requirement id, or a Claim id when subject == "claim"
    subject: Literal["requirement", "claim"] = "requirement"
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


LOCATOR_SCHEMA = {  # what a model is shown for `locator`: one flat object, no $ref, no oneOf
    "type": "object",
    "title": "Locator",
    "description": ("Where the quote is. A file: kind=\"file_range\" with path, startLine, endLine (as shown by "
                    "read_file). A report page: kind=\"doc_span\" with page. A recorded capture: "
                    "kind=\"http_capture\" with url, or kind=\"git_object\" with commitSha."),
    "properties": {
        "kind": {"type": "string", "enum": ["file_range", "doc_span", "http_capture", "git_object"]},
        "path": {"type": "string"}, "startLine": {"type": "integer"}, "endLine": {"type": "integer"},
        "page": {"type": "integer"}, "url": {"type": "string"}, "commitSha": {"type": "string"},
    },
    "required": ["kind"],
}


class LocatorDraft(BaseModel):
    """A proposed citation. Becomes Evidence only if the resolver finds `quote` there."""

    locator: EvidenceLocator
    quote: str

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        """The model-facing schema. The stored type is a discriminated union, which pydantic renders as
        oneOf + $ref; Strands' tool-schema flattener drops the $defs and leaves the refs dangling, and
        OpenAI-compatible endpoints reject that outright. The model gets one flat object instead;
        validation below still builds the strict locator from it."""
        schema = handler.resolve_ref_schema(handler(core_schema))
        schema["properties"]["locator"] = LOCATOR_SCHEMA
        return schema

    @model_validator(mode="before")
    @classmethod
    def _tolerate_model_output(cls, data):
        """Fill the bookkeeping fields a model omits, so a real citation is not lost to a missing tag.

        Models routinely leave out the `kind` discriminator, `artifactId`, and the commit SHA (which
        they cannot know anyway — the resolver overwrites it from the checkout). All three are
        inferable or supplied by us, and none of them is evidence.

        Tolerance stops here. The stored types stay strict, and the locator must still survive the
        resolver, which is where trust is actually decided.
        """
        if not isinstance(data, dict):
            return data
        loc = data.get("locator")
        if not isinstance(loc, dict):
            return data
        loc = dict(loc)

        if not loc.get("kind"):
            if "page" in loc:
                loc["kind"] = "doc_span"
            elif "url" in loc or "status" in loc:
                loc["kind"] = "http_capture"
            elif "authorHash" in loc or "committedAt" in loc:
                loc["kind"] = "git_object"
            elif "path" in loc or "startLine" in loc:
                loc["kind"] = "file_range"
            else:
                return data

        if loc["kind"] == "file_range":
            loc.setdefault("commitSha", "")  # the resolver replaces this with the checkout's
            loc.setdefault("startLine", 1)
            loc.setdefault("endLine", loc["startLine"])
        elif loc["kind"] == "doc_span":
            loc.setdefault("artifactId", "report")
        return {**data, "locator": loc}


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
    scale: Scale = Scale()
    sourceQuote: str = ""  # the rubric line this came from; compile.py finds it to compute sourceSpan
    verifiable: bool
    unverifiableReason: str | None = None
    proposedBy: Literal["evaluator", "repoman"] = "evaluator"


class CompiledRubric(BaseModel):
    requirements: list[RequirementDraft] = Field(min_length=1)


class ContradictionDraft(BaseModel):
    """A claim the submission makes about itself that its own code does not support."""

    requirementId: str
    summary: str
    evidence: list[LocatorDraft] = Field(min_length=1)  # the claim's locator, resolved like any other


class Contradictions(BaseModel):
    contradictions: list[ContradictionDraft] = []


# --- The human's contribution --------------------------------------------------


class Decision(BaseModel):
    id: str = Field(default_factory=new_id)
    submissionId: str
    requirementId: str
    evaluatorId: str
    score: float | None = None  # the human's number. RepoMan never writes here.
    level: str | None = None  # the chosen Level.label when the scale is "levels"
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
    notes: list[str] = []  # stages that degraded. A run that did less than usual must say so.


RunStage = Literal["queued", "acquire", "probe", "verify", "claims", "contradict", "done", "failed"]


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
    checkClaims: bool = True  # also verify what each submission claims about itself (one more agent run per claim)
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
    try:
        Scale(kind="levels")
        raise SystemExit("schema accepted a levels scale with no levels")
    except ValidationError:
        pass
    print("types ok")
