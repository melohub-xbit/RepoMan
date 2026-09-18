# RepoMan — agent brief

> Read this first. It is the entry point for any AI coding agent working in this
> repository (Claude Code, Cursor, Codex, Copilot, Aider, or anything else).
> `CLAUDE.md` points here.

## What we are building

RepoMan is an **evidence-gathering copilot for people who evaluate software they
did not write** — professors, TAs, hackathon judges, hiring reviewers.

It takes a submission (GitHub repo, README, project report, demo video, slide
deck, architecture diagram, deployed URL) plus a rubric, and produces
**Requirement → Evidence → Finding** records that a human then scores.

The one-line thesis, which constrains every decision below:

> **RepoMan does not judge people's work. It does the tedious investigation
> required before a human can judge it well.**

## The five invariants

Break any of these and the product stops being the product. If a change you are
about to make violates one, stop and raise it rather than working around it.

1. **RepoMan never produces a score.** Not a grade, not a rank, not a
   recommendation, not a "suggested band". It produces findings with evidence.
   The human scores. If a feature request implies a number, push back.
2. **No finding without a locator.** Every claim resolves to at least one
   `EvidenceLocator` (file range, doc span, media span, HTTP capture, or git
   object). This is enforced by schema, not by prompt. See
   [`docs/03-data-model.md`](docs/03-data-model.md).
3. **Locators are verified, not trusted.** Every locator a model returns is
   re-resolved against the content-addressed blob store before it is stored, and
   the quoted text must match. A citation that does not resolve is dropped and
   logged, never rendered.
4. **`unverified` is a real answer.** "We looked and found nothing" is a correct,
   valuable output. Never let a model guess to fill a gap. One bluffed citation
   destroys evaluator trust for the whole session.
5. **All submitted content is hostile input.** It enters as untrusted user-turn
   data, never as instruction, and it never steers a privileged tool. See
   [`docs/05-security-model.md`](docs/05-security-model.md).

## Documentation map

Read the doc that covers your task before writing code. They are short.

| Doc | Read it when |
|---|---|
| [`docs/00-product-brief.md`](docs/00-product-brief.md) | You need the why, the users, and the finding model |
| [`docs/01-value-and-scope.md`](docs/01-value-and-scope.md) | You are deciding what to build next, or arguing about scope |
| [`docs/02-architecture.md`](docs/02-architecture.md) | You are touching module boundaries, the pipeline, or deployment |
| [`docs/03-data-model.md`](docs/03-data-model.md) | You are touching types, schemas, or storage. **Most changes start here.** |
| [`docs/04-model-orchestration.md`](docs/04-model-orchestration.md) | You are writing anything that calls Claude |
| [`docs/05-security-model.md`](docs/05-security-model.md) | You are handling submitted content or executing submitted code |
| [`docs/06-build-plan.md`](docs/06-build-plan.md) | You want to know what is in scope right now |
| [`docs/07-open-questions.md`](docs/07-open-questions.md) | You hit an unresolved design decision |

## Repository layout

A pnpm + uv monorepo. TypeScript owns everything a human touches; Python owns
everything that parses a file format.

```
packages/
  core/         ts + py  Domain types, EvidenceLocator, finding state machine. No I/O.
  intake/       ts       Submission source adapters (GitHub, Drive, CSV, LTI, zip)
  workspace/    ts       Next.js evaluator UI
  export/       ts       Evidence packet, CSV, feedback report, cohort report
  cli/          ts       Local-first surface
services/
  acquire/      py       Clone, download, transcribe, extract → blob store
  probes/       py       Deterministic checks (build, test, deps, git, injection)
  index/        py       tree-sitter chunking, embeddings, hybrid retrieval
  verify/       py       Rubric compiler + model orchestration
docs/                    This documentation set
```

## Conventions

**Language boundaries.** TypeScript and Python each own a mirror of the `core`
types. They must stay in sync — the canonical definition lives in
`docs/03-data-model.md`, and both mirrors are generated from a single JSON Schema
in `packages/core/schema/`. Never hand-edit one mirror without the other.

**Ports, not conditionals.** Local-first and cloud differ only in adapter
implementations behind five interfaces (`BlobStore`, `MetaStore`, `JobQueue`,
`Sandbox`, `ModelClient`). Never write `if (isCloud)` in business logic.

**Probes are pure.** Every deterministic probe is a function from artifacts to
evidence with no hidden state, so it can be unit-tested against a fixture repo.
If a probe needs the network, it takes a client as a parameter.

**Content addressing.** Everything acquired is stored by SHA. Locators reference
SHAs, not mutable paths or branch names, so an evidence packet stays valid after
the student force-pushes.

**Model calls live in `services/verify` only.** No other module calls Claude. If
you find yourself wanting a model call elsewhere, the boundary is wrong.

**Naming.** `Submission` (the thing being evaluated), `Artifact` (one file or
source within it), `Requirement` (one compiled rubric line), `Evidence` (a
located fact), `Finding` (requirement + evidence + state), `Decision` (the
human's output). Do not invent synonyms — these names appear in the UI, the
schema, and the exports.

## What not to do

- Do not add a scoring, ranking, or "suggested grade" feature.
- Do not let a model report a PDF page number. Use the API's `citations` feature,
  which returns `page_location` / `char_location`. See `docs/04-model-orchestration.md`.
- Do not run submitted code outside the sandbox, ever, including "just to check".
- Do not put submission content into a system prompt.
- Do not silently truncate a repository to fit a context window. Budget
  retrieval explicitly and record what was excluded.
- Do not add a dependency to `packages/core`. It stays dependency-free.

## Current status

Pre-implementation. The documentation set is complete; no code has been written
yet. Start from [`docs/06-build-plan.md`](docs/06-build-plan.md) — the Day 1
spine is the vertical slice that proves the thesis end to end.
