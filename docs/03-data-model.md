# 03 — Data model

This document is the **source of truth for types**. `repoman/core/types.py`
holds the pydantic models that implement it. Change this document first, then
the models, in the same commit.

---

## EvidenceLocator — the spine

One tagged union, used by every probe and every model call. A finding that cannot
produce at least one of these is rejected before it reaches the evaluator,
**enforced by schema, not by prompt**.

```ts
type EvidenceLocator =
  | { kind: "file_range";   path: string; startLine: number; endLine: number;
      commitSha: string }

  | { kind: "doc_span";     artifactId: string; page: number }   // 1-indexed

  | { kind: "http_capture"; url: string; status: number; capturedAt: string;
      title?: string }

  | { kind: "git_object";   commitSha: string; authorHash: string;
      committedAt: string }
```

Every variant renders to something a human can open:

```
file_range   → github.com/<org>/<repo>/blob/<commitSha>/<path>#L41-L68
             → for a zip submission: the workspace's own file view at those lines
doc_span     → workspace page view with the quote highlighted
http_capture → the recorded status, title and timestamp
git_object   → commit permalink
```

`media_span` (video) is out of v1 and removed rather than left as dead schema.
Add it back with the transcription stage.

### Rules

1. **`commitSha` is required, not optional.** Locators reference the pinned
   checkout, never a branch name. An evidence packet must stay valid after the
   student force-pushes.
2. **`authorHash`, never a raw email.** Hashing happens in `intake` when the git
   log is read, before anything else sees the data.
3. **Resolution before storage.** Every locator is re-read from the checkout (or
   `report_pages.json`) and `Evidence.quote` must be found there. A locator that
   does not resolve is dropped and counted — never rendered, never stored. See
   [`04-model-orchestration.md`](04-model-orchestration.md), Rule 2.
4. **The quote is the proof.** A `doc_span` is page + quote; a `file_range` is
   lines + quote. The model proposes both; the resolver confirms the quote is
   really there. This is what makes a model-proposed page number safe to store.

---

## Core entities

```ts
type Submission = {
  id: string;
  batchId: string;            // the cohort / event this belongs to
  source: "github" | "zip";
  repoUrl?: string;           // for permalinks; absent for zip
  commitSha: string;          // the pinned checkout every file_range refers to
  artifacts: Artifact[];
  acquiredAt: string;
  // Identity fields are separated so blind mode can strip them before the run.
  identity: SubmissionIdentity | null;
};

type SubmissionIdentity = {
  team?: string; members?: string[]; institution?: string;
};

type Artifact = {
  id: string;
  submissionId: string;
  kind: "repo" | "readme" | "report" | "deploy";   // a deck exported to PDF is a "report"
  uri: string;                // original location, for provenance
  sha256: string | null;      // null for `deploy`, which is captured not stored
  quarantined: boolean;       // set by the injection scanner; excluded from tools
};
```

```ts
type Rubric = {
  id: string; version: number;
  sourceText: string;         // the evaluator's original prose, kept verbatim
  requirements: Requirement[];
  compiledBy: string;         // model ID + prompt hash, for replay
};

type Scale =
  | { kind: "check" }                                  // met / not met → 0 or weight
  | { kind: "points" }                                 // 0..weight
  | { kind: "levels"; levels: { label: string; description?: string; points: number }[] };

type Requirement = {
  id: string; rubricId: string;
  title: string;
  statement: string;          // the checkable form — the only part a model ever sees
  weight: number;             // max marks; for "levels", the highest level's points
  scale: Scale;               // how the human scores it. Rendered in the decision strip, never sent to a model
  sourceSpan: { startChar: number; endChar: number } | null;  // back into sourceText; null when typed in directly
  verifiable: boolean;        // false = "creativity"; stays 100% human
  unverifiableReason?: string;
  proposedBy: "evaluator" | "repoman";   // "repoman" when the compiler decomposed a holistic line
};
```

`verifiable: false` requirements are **shown to the evaluator and never sent to a
model**. Pretending to check them is the fastest way to lose trust.

`scale` is the evaluator's instrument, not RepoMan's. Level descriptions ("some
tests" / "comprehensive tests") are band language; feeding them to the verify
agent invites it to name a band in `summary`, which is a score by another name.
The agent gets `statement`; the human gets the levels. Requirements enter a
rubric three ways and land in the same table: compiled from pasted prose (the
compiler also infers `scale` from "— 15 marks" or band descriptors), typed in one
at a time (`sourceSpan: null`), or both.

**`sourceSpan` is computed, never returned.** The compiler's draft type
(`RequirementDraft`) carries `sourceQuote` — the verbatim line of the evaluator's
rubric a requirement came from — and `verify/compile.py` finds that quote in
`sourceText` to produce the offsets. This is the same rule as the locator
resolver: a model is asked what it saw, not where it was. Character offsets a
model counts itself are wrong often enough to make the rubric's provenance a lie,
and a quote that cannot be found in `sourceText` yields `sourceSpan: null` rather
than a fabricated range.

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

type Claim = {
  id: string; submissionId: string;
  statement: string;          // the checkable form of something the submission says about itself
  source: Evidence;           // the claim's own words at their location (README line, report page)
};

type Finding = {
  id: string;
  submissionId: string;
  requirementId: string;      // a Requirement id, or a Claim id when subject is "claim"
  subject: "requirement" | "claim";
  state: FindingState;
  flagged: FlagKind[];        // orthogonal; may co-occur with any state
  summary: string;            // one or two sentences, evaluator-facing
  evidence: Evidence[];       // minItems: 1 — enforced at the schema level
  confidence: "high" | "medium" | "low";
  confidenceReason: string;
  searchExhausted: boolean;   // true when the tool-call cap ended the search
  questions: string[];        // viva questions for the submitter; empty when VERIFIED
  producedBy: string;         // model ID + prompt hash
};

type FlagKind =
  | "PROMPT_INJECTION" | "TIMELINE_ANOMALY" | "COHORT_SIMILARITY"
  | "FORK_SUSPECTED" | "CONTRIBUTION_SKEW";
```

**`evidence` has `minItems: 1`.** A finding with no locator is not a weak finding
to be filtered later — it is a validation error that never reaches the database.

**Claims are the submission's rubric.** The claims pass extracts what the README
and report say the project does, each with a resolved locator for the sentence,
and verifies every claim exactly like a requirement. A claim finding is never
scored and never counts toward coverage; it tells the evaluator whether the
submission's own description holds up. Lives in `runs/<runId>/claims.json`.

`UNVERIFIED` findings still carry evidence: the evidence of *where we looked*.
A negative result is a claim about the search, and it must be as auditable as a
positive one.

```ts
type Decision = {
  id: string;
  submissionId: string; requirementId: string;
  evaluatorId: string;
  score: number | null;       // the human's number. RepoMan never writes here.
  level?: string;             // the chosen Level.label when the scale is "levels"
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
  commitSha: string;
  artifactShas: Record<string, string>;
  probeVersions: Record<string, string>;
  modelId: string;
  promptHashes: Record<string, string>; // pass → template hash
  startedAt: string; finishedAt: string;
  usage: UsageRecord[];                 // per agent run; see 04
  mismatches: number;                   // locators dropped by the resolver
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

- Everything is JSON under `runs/<runId>/` in the `Store`; the layout is in
  [`02-architecture.md`](02-architecture.md). No database.
- `Evidence.quote` is denormalized deliberately. It is what the export renders
  when the original repository has gone private.
- Everything except `Decision` and `Precedent` is derivable from a `RunManifest`
  plus the checkout. Those two are the human's contribution; they live in their
  own file and are never overwritten by a re-run.
- Findings are immutable once written. A re-run writes a new `runId`; the
  submission page shows the latest and links the history. See
  [`07-open-questions.md`](07-open-questions.md) §1.
