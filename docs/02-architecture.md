# 02 — Architecture

## Pipeline

Seven stages, strictly ordered. Each stage's output is the next stage's only
input, which is what makes a run replayable from its manifest.

```
01 Intake          Adapter normalizes any source into Submission + Artifact[]
02 Acquire         Clone, download, transcribe, extract — content-addressed by SHA
03 Compile rubric  Prose → Requirement graph; unverifiable criteria marked
04 Probe           Deterministic checks: build, test, deploy, deps, git, injection
05 Index           Chunk + embed code, docs, transcript — chunks carry locators
06 Verify          Per-requirement retrieval → Finding[]; no locator, no finding
07 Human decides   Workspace, overrides, precedent, export
```

Stages 04 and 05 run in parallel per submission. Stage 06 fans out per
requirement. Only stage 07 is not automated, and that is the design, not a gap.

**Replayability.** Every run writes a `RunManifest` recording the input SHAs, the
compiled rubric version, the probe versions, the model IDs, and the prompt
template hashes. Given a manifest, a run can be reproduced or diffed. This is
what makes an appeal answerable six months later.

## Modules

A pnpm + uv monorepo. The language split follows a real boundary: **TypeScript
owns everything a human touches, Python owns everything that parses a file
format.** Python has the better libraries for tree-sitter, PDF layout,
transcription, and similarity; TypeScript has the better story for the UI,
adapters, and a distributable CLI.

| Module | Lang | Responsibility |
|---|---|---|
| `packages/core` | ts + py | Domain types, `EvidenceLocator`, finding state machine, run manifest. **Zero I/O, zero dependencies.** |
| `packages/intake` | ts | Source adapters: GitHub App, Classroom, Drive/Sheets, Devpost/Devfolio CSV, LTI, zip. One interface out. |
| `services/acquire` | py | Clone, fetch, whisper transcription, PDF/PPTX extraction. Writes the content-addressed blob store. |
| `services/probes` | py | Deterministic checks. Each is a pure function from artifacts to evidence. |
| `services/index` | py | tree-sitter chunking, embeddings, hybrid BM25 + vector retrieval. Chunks carry locators. |
| `services/verify` | py | Rubric compiler and model orchestration. **The only module that calls Claude.** |
| `packages/workspace` | ts | Next.js evaluator UI: queue, evidence panel, split code/video view, overrides. |
| `packages/export` | ts | LTI grade passback, CSV, evidence packet, student feedback, cohort report. |
| `packages/cli` | ts | The local-first surface. Same core, adapters swapped to SQLite and local disk. |

### Dependency rules

- `core` depends on nothing. Adding a dependency to it is a design error.
- Nothing imports `verify` except the job runner. Model calls do not leak.
- `probes` may not import `index` or `verify`. Determinism is the point.
- The TypeScript and Python mirrors of `core` are generated from one JSON Schema
  in `packages/core/schema/`. Never hand-edit one mirror alone.

## Local-first and cloud are the same code

Five ports separate the two deployment tracks. This is what makes the dual
deployment a genuine architectural claim rather than the same application
deployed twice.

| Port | Local-first | Cloud (AWS) | Why it matters |
|---|---|---|---|
| `BlobStore` | Local filesystem | S3 | Submissions are the sensitive asset |
| `MetaStore` | SQLite + sqlite-vec | Aurora Postgres + pgvector | One machine versus a cohort of 500 |
| `JobQueue` | In-process worker pool | SQS + ECS Fargate | Fan-out is the only thing that scales |
| `Sandbox` | Docker, no network | Fargate task, egress denied | Running submitted code is the real risk |
| `ModelClient` | Claude API direct | Bedrock Mantle client | Same request shape either way |

**Never write `if (isCloud)` in business logic.** If a behavior differs between
tracks, it belongs behind one of these five interfaces. If it does not fit behind
one of them, that is a signal the port set is wrong — raise it rather than adding
a conditional.

### ModelClient specifics

On AWS the client is `AnthropicBedrockMantle(aws_region=...)` with an
`anthropic.`-prefixed model ID (`anthropic.claude-opus-5`). Locally it is the
plain `Anthropic()` client with the bare ID (`claude-opus-5`). Both expose the
same `messages.create` / `.stream` surface, so `services/verify` never learns
which one it is talking to. The port's only job is resolving the model ID and
constructing the client.

See [`04-model-orchestration.md`](04-model-orchestration.md).

## Data flow

```
Submission ──┬─→ Artifact (repo)      ──→ blobs ──┬─→ probes ──→ Evidence
             ├─→ Artifact (report)    ──→ blobs ──┤
             ├─→ Artifact (video)     ──→ blobs ──┤
             ├─→ Artifact (deck)      ──→ blobs ──┘
             └─→ Artifact (deploy)    ──→ capture ──→ Evidence

Rubric ──→ compiler ──→ Requirement[]
                             │
                             ↓
          Requirement × retrieved chunks ──→ verify ──→ Finding
                                                          │
                                                          ↓
                                            Evaluator ──→ Decision ──→ Precedent
```

`Evidence` is produced by both probes (deterministic) and verify (model-derived),
and both go through the same locator resolution before storage. The evaluator
cannot tell which is which from the schema — but the UI shows the provenance,
because deterministic evidence deserves more trust and evaluators should know.

## Concurrency model

- One job per `(submission, stage)` pair. Stages 04 and 05 are independent.
- Stage 06 fans out to one job per `(submission, requirement)`. This is where the
  prompt cache pays off — see `04-model-orchestration.md`.
- Sandbox jobs get a hard wall-clock cap and are always the tail latency. Run
  them early and do not block the rest of the pipeline on them.
- A failed probe produces an `UNVERIFIED` finding with the failure recorded, never
  a missing finding. Silence is indistinguishable from absence, which violates
  invariant 4.

## AWS mapping (cloud track)

| Concern | Service |
|---|---|
| Blob store | S3, versioning on |
| Metadata + vectors | Aurora Serverless v2 Postgres + pgvector |
| Queue | SQS, one queue per stage, DLQ on each |
| Workers | ECS Fargate |
| Sandbox | ECS Fargate task, no egress except a package-registry allowlist |
| Models | Bedrock Mantle client, or Claude API direct |
| Secrets | Secrets Manager; never mounted into a sandbox task |

## Why this shape

Three properties are load-bearing and everything else was chosen to preserve
them:

1. **Every finding is traceable to bytes.** Content addressing plus locator
   resolution means an evidence packet stays valid after a force-push.
2. **Deterministic and model-derived evidence are interchangeable downstream.**
   This lets us move checks from the expensive path to the cheap path over time
   without touching the UI or the exports.
3. **The human is a pipeline stage, not a consumer.** Overrides feed back in as
   precedent, which is why `Decision` and `Precedent` are first-class types rather
   than UI state.
