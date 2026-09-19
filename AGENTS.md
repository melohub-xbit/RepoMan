# RepoMan — agent brief

> Read this first. It is the entry point for any AI coding agent working in this
> repository (Claude Code, Cursor, Codex, Copilot, Aider, or anything else).
> `CLAUDE.md` points here.

## What we are building

RepoMan is an **evidence-gathering copilot for people who evaluate software they
did not write** — professors, TAs, hackathon judges, hiring reviewers.

It takes a submission (GitHub repo or zip, README, project report or deck as
PDF, deployed URL) plus a rubric, and produces **Requirement → Evidence →
Finding** records that a human then scores.

**Build constraints:** two days, one developer, $100 of AWS credits, and the
hackathon's service list ([`docs/02-architecture.md`](docs/02-architecture.md)).
The simplest thing that satisfies the invariants wins every argument.

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
   re-resolved against the pinned checkout before it is stored, and the quoted
   text must be found there. A citation that does not resolve is dropped and
   counted, never rendered.
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

One Python 3.12 package. Server-rendered UI in the same process.

```
repoman/
  core/        Domain types (pydantic), EvidenceLocator, resolver, finding states. No I/O.
  intake/      GitHub URL / zip → Submission; git clone at pinned commit; pypdf for reports
  probes/      Deterministic checks (deps, tests, git, injection, deploy). Pure functions.
  verify/      Rubric compiler, Strands agent + read-only tools, contradiction pass
  store/       Store port: LocalStore | S3Store. JSON + bytes, nothing else.
  web/         FastAPI + Jinja2 evaluator UI: batch list, findings, overrides, export
  cli.py       repoman run <repo-or-zip> --rubric r.md
fixtures/      Small real repos incl. injected/ (planted payload — never delete)
infra/         Dockerfile, App Runner config, deploy script
docs/          This documentation set
```

## Conventions

**Types.** `docs/03-data-model.md` is canonical; `repoman/core/types.py` is
its pydantic implementation. Change the doc and the models in the same commit.

**Ports, not conditionals.** Local-first and cloud differ in exactly two places:
`store/` (`LocalStore` | `S3Store`) and `verify/model.py` (`OllamaModel` |
`BedrockModel`), both chosen from environment variables at startup. Never write
`if is_cloud` in business logic.

**Probes are pure.** Every deterministic probe is a function from artifacts to
evidence with no hidden state, so it can be unit-tested against a fixture repo.
If a probe needs the network, it takes a client as a parameter.

**Pinned commits.** Every `file_range` carries the checkout's `commitSha`, so a
permalink stays valid after the student force-pushes.

**Model calls live in `repoman/verify` only.** No other module imports Strands.
If you find yourself wanting a model call elsewhere, the boundary is wrong.

**Agent tools are read-only.** `tree`, `grep`, `read_file`, `read_report_page`,
`list_deps`, `probe_results`. Adding a tool that takes a URL, writes, or spawns a
process violates boundary 2 in `docs/05-security-model.md`.

**Naming.** `Submission` (the thing being evaluated), `Artifact` (one file or
source within it), `Requirement` (one compiled rubric line), `Evidence` (a
located fact), `Finding` (requirement + evidence + state), `Decision` (the
human's output). Do not invent synonyms — these names appear in the UI, the
schema, and the exports.

## What not to do

- Do not add a scoring, ranking, or "suggested grade" feature.
- Do not store a `doc_span` whose quote the resolver could not find on that page.
- Do not run submitted code, ever, including "just to check". v1 executes
  nothing; see `docs/05-security-model.md`.
- Do not put submission content into a system prompt or a user turn. It reaches
  the model only as `<untrusted>` tool results.
- Do not silently truncate. The tool-call cap is recorded as `searchExhausted`.
- Do not add a dependency to `repoman/core` beyond pydantic.
- Do not add AWS services beyond S3, Bedrock and App Runner without a reason
  written in `docs/07-open-questions.md`. Breadth of services is not the demo.

## Current status

Pre-implementation. The documentation set is complete and scoped to a two-day
build; no code has been written yet. Start from
[`docs/06-build-plan.md`](docs/06-build-plan.md) — the Day 1 spine is the
vertical slice that proves the thesis end to end.
