# 06 — Build plan

The vertical slice comes first: **one submission, one rubric, one finding
rendered with a working permalink.** Everything after that is breadth on a spine
that already runs end to end.

Resist building any layer wide before the spine is closed. A beautiful workspace
with no locator resolution is a demo; a terminal printout with a permalink that
opens is a product.

---

## Day 1 — Spine

Goal: `repoman run ./fixtures/springboot --rubric fixtures/rubric.md` prints a
finding whose GitHub permalink opens to the right lines.

- [ ] `packages/core` types and `EvidenceLocator`, generated from JSON Schema
      into both language mirrors
- [ ] The locator **resolver** — re-reads from the blob store at SHA, compares
      the quote, drops on mismatch. Build this before anything produces locators.
- [ ] Content-addressed blob store behind the `BlobStore` port, local FS impl
- [ ] Zip and public-GitHub intake; clone, README, file tree
- [ ] Rubric compiler producing an editable checklist, with `verifiable: false`
      working on a "creativity" criterion
- [ ] One verify pass with schema-enforced citations (`minItems: 1`)
- [ ] Findings into SQLite behind the `MetaStore` port
- [ ] CLI that prints findings to the terminal — **no UI yet**

**Exit criterion:** a deliberately false claim in the fixture README produces a
`CONTRADICTED` finding, and the cited permalink opens to the right lines.

---

## Day 2 — Credibility

Goal: the findings stop looking like a language model and start looking like an
investigation.

- [ ] Deterministic probes: dependency reachability, test discovery, git
      timeline, deploy liveness
- [ ] Injection scanner and the `FLAGGED` state; `fixtures/injected/` passing
- [ ] Sandbox port with the Docker implementation, no network
- [ ] PDF ingestion using API citations for `doc_span`
- [ ] Video transcription with timestamps → `media_span`
- [ ] Workspace: queue, evidence panel, split code/video view, override +
      precedent

**Exit criterion:** a finding that cites code, the report, and the demo video at
once, with all three links landing in the right place.

---

## Day 3 — Scale

Goal: the cloud track is real and the cost number is measured.

- [ ] Cloud adapters: S3, Aurora + pgvector, SQS, Fargate
- [ ] Batch API fan-out across a cohort
- [ ] Cross-submission similarity and the cohort report
- [ ] Evidence packet and student feedback export
- [ ] Instrument `usage` and publish a real cost per submission

**Exit criterion:** a 50-submission batch completes, and we can state the actual
dollar cost rather than the estimate in [`04`](04-model-orchestration.md).

---

## Deliberately cut from v1

Recording these so they do not get re-argued mid-build.

| Cut | Why |
|---|---|
| **LTI 1.3 grade passback** | Highest adoption value, but certification work is days. Ship CSV export; LTI is the first post-hackathon item. |
| **Browser extension** | Needs a stable API surface first. |
| **Calibration and drift detection** | Needs real multi-evaluator data to be anything but a mock. |
| **Cross-institution similarity** | Strong feature, hard data-sharing conversation. Vision, not v1. |
| **Anything producing a number** | Permanent. Holding this line *is* the demo. |

---

## The demo

Run RepoMan on RepoMan's own submission, live.

It is immediately legible to any audience, it needs no setup explanation, and it
forces honesty about our own unverified claims — which is precisely the argument
the product makes. Rehearse the failure case too: show a requirement we cannot
verify and let it say so.

Sequence:

1. Submission and rubric in, evidence workspace out — thirty seconds.
2. One `CONTRADICTED` finding, opened to the cited code and the cited report
   page side by side.
3. The planted injection payload caught and flagged.
4. One `UNVERIFIED` finding, explained as a feature rather than apologised for.
5. Evaluator overrides a finding; precedent applies to the rest of the batch.

---

## Metrics to instrument from day one

- **Time to decision** per submission — the headline metric, not accuracy.
- **Evidence coverage** distribution across a cohort.
- **Locator mismatch rate** — a rising rate means a prompt or retrieval
  regression.
- **Cache hit rate** (`usage.cache_read_input_tokens`) — zero means the prefix
  broke.
- **Cost per completed evaluation**, not per request.
- **Override rate** per requirement — a criterion overridden every time is a
  rubric compiler bug.
