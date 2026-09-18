# 03 — Data model

This document is the **source of truth for types**. The TypeScript and Python
mirrors in `packages/core` are generated from `packages/core/schema/*.json`, and
that schema is generated from what is written here. Change this document first.

---

## EvidenceLocator — the spine

One tagged union, used by every probe and every model call. A finding that cannot
produce at least one of these is rejected before it reaches the evaluator,
**enforced by schema, not by prompt**.

```ts
type EvidenceLocator =
  | { kind: "file_range";   artifactId: string; path: string;
      startLine: number; endLine: number; blobSha: string }

  | { kind: "doc_span";     artifactId: string; page?: number;
      startChar: number; endChar: number }

  | { kind: "media_span";   artifactId: string; startMs: number;
      endMs: number; transcriptRef: string }

  | { kind: "http_capture"; artifactId: string; url: string; status: number;
      capturedAt: string; screenshotSha: string }

  | { kind: "git_object";   artifactId: string; commitSha: string;
      authorEmailHash: string; committedAt: string }
```

Every variant renders to something a human can open:

```
file_range   → github.com/<org>/<repo>/blob/<blobSha>/<path>#L41-L68
media_span   → workspace deep-link, seeks the player to 02:14
doc_span     → highlighted span in the embedded PDF viewer
http_capture → stored screenshot + the recorded status and timestamp
git_object   → commit permalink
```

### Rules

1. **`blobSha` and `commitSha` are required, not optional.** Locators reference
   immutable content, never a branch name or a mutable path. An evidence packet
   must stay valid after the student force-pushes.
2. **`authorEmailHash`, never a raw email.** Hashing happens at acquisition time
   in `services/acquire`, before anything else sees the data.
3. **Resolution before storage.** Every locator is re-read from the blob store at
   the given SHA and the quoted text must match. A locator that does not resolve
   is dropped and logged — never rendered, never stored.
4. **`doc_span` is never produced by a model.** See below.

### Where doc spans come from

We do not ask the model to report PDF page numbers; it will get them wrong. The
report is sent as a `document` content block with `citations: {enabled: true}`,
and the API returns `page_location` (`start_page_number` / `end_page_number`,
1-indexed) and `char_location` (`start_char_index` / `end_char_index`) for every
cited passage. We map those directly onto `doc_span`.

The locator is produced by the platform, not guessed by the model. This removes
an entire class of fabricated citation, and it is the single highest-value
implementation detail in the system. See
[`04-model-orchestration.md`](04-model-orchestration.md).

---

## Core entities

```ts
type Submission = {
  id: string;
  batchId: string;            // the cohort / event this belongs to
  externalRef: string;        // the adapter's own ID, for idempotent re-import
  source: IntakeSource;       // which adapter produced this
  artifacts: Artifact[];
  acquiredAt: string;
  // Identity fields are separated so blind mode can strip them pre-retrieval.
  identity: SubmissionIdentity | null;
};

type SubmissionIdentity = {
  team?: string; members?: string[]; institution?: string;
};

type Artifact = {
  id: string;
  submissionId: string;
  kind: "repo" | "readme" | "report" | "deck" | "video" | "diagram" | "deploy";
  uri: string;                // original location, for provenance
  blobSha: string | null;     // null for `deploy`, which is captured not stored
  mediaType: string;
  bytes: number;
  quarantined: boolean;       // set by the injection scanner
};
```

```ts
type Rubric = {
  id: string; version: number;
  sourceText: string;         // the evaluator's original prose, kept verbatim
  requirements: Requirement[];
  compiledBy: string;         // model ID + prompt hash, for replay
};

type Requirement = {
  id: string; rubricId: string;
  title: string;
  statement: string;          // the checkable form
  weight: number;
  sourceSpan: { startChar: number; endChar: number };  // back into sourceText
  verifiable: boolean;        // false = "creativity"; stays 100% human
  unverifiableReason?: string;
};
```

`verifiable: false` requirements are **shown to the evaluator and never sent to a
model**. Pretending to check them is the fastest way to lose trust.

```ts
type Evidence = {
  id: string;
  submissionId: string;
  locator: EvidenceLocator;
  quote: string;              // the exact text at the locator, re-read not echoed
  provenance: "probe" | "model";
  probeId?: string;           // set when provenance is "probe"
  resolvedAt: string;
};

type FindingState =
  | "VERIFIED" | "PARTIAL" | "UNVERIFIED" | "CONTRADICTED";

type Finding = {
  id: string;
  submissionId: string;
  requirementId: string;
  state: FindingState;
  flagged: FlagKind[];        // orthogonal; may co-occur with any state
  summary: string;            // one or two sentences, evaluator-facing
  evidence: Evidence[];       // minItems: 1 — enforced at the schema level
  confidence: "high" | "medium" | "low";
  confidenceReason: string;
  producedBy: string;         // model ID + prompt hash
};

type FlagKind =
  | "PROMPT_INJECTION" | "TIMELINE_ANOMALY" | "COHORT_SIMILARITY"
  | "FORK_SUSPECTED" | "CONTRIBUTION_SKEW";
```

**`evidence` has `minItems: 1`.** A finding with no locator is not a weak finding
to be filtered later — it is a validation error that never reaches the database.

`UNVERIFIED` findings still carry evidence: the evidence of *where we looked*.
A negative result is a claim about the search, and it must be as auditable as a
positive one.

```ts
type Decision = {
  id: string;
  submissionId: string; requirementId: string;
  evaluatorId: string;
  score: number | null;       // the human's number. RepoMan never writes here.
  note: string;
  overrodeFindingId?: string;
  decidedAt: string;
};

type Precedent = {
  id: string; batchId: string; requirementId: string;
  rule: string;               // "accept .env.example as config-management evidence"
  derivedFromDecisionId: string;
  appliesFrom: string;        // applied to submissions processed after this
};
```

`Precedent.appliesFrom` matters: a precedent set halfway through a batch is
applied forward, and the workspace surfaces which earlier submissions predate it
so the evaluator can choose to re-run them. Silently applying it retroactively —
or silently not — are both unfair in different directions, so we make it visible.

```ts
type RunManifest = {
  id: string; submissionId: string; rubricId: string; rubricVersion: number;
  artifactShas: Record<string, string>;
  probeVersions: Record<string, string>;
  modelIds: Record<string, string>;     // stage → model ID
  promptHashes: Record<string, string>; // stage → template hash
  startedAt: string; finishedAt: string;
  usage: UsageRecord[];                 // per model call; see 04
};
```

---

## Derived values

**Evidence coverage** is the one number RepoMan produces, and it is a count, not
a judgment:

```
coverage = |{r : findings[r].state == VERIFIED}| / |{r : r.verifiable}|
```

Rendered as "8 of 12 requirements have verified evidence." It is objective,
sortable, and defensible. It is explicitly **not** a score and must never be
presented as one, weighted by rubric weight, or converted to a percentage grade.

---

## Storage notes

- Blobs are content-addressed: `blobs/<sha256>`. Deduplication across a cohort is
  free and significant — eighty submissions of the same assignment share most of
  their dependency trees.
- `Evidence.quote` is denormalized deliberately. It is what the export renders
  when the original repository has gone private.
- Everything except `Decision` and `Precedent` is derivable from a `RunManifest`
  plus the blob store. Those two are the human's contribution and are backed up
  separately.
