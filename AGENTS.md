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
  probes/      Deterministic checks (deps, tests, git, fork, injection, deploy) + similarity. Pure functions.
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

**Cross-submission similarity is computed, never stored on a run.** A run writes
its own `fingerprint.json`; the pairing is derived from the batch on demand
(`pipeline.batch_similarity`). The comparison's answer changes as the batch
fills — the fifth submission can reveal that the first two were copies — and a
later submission must never rewrite an earlier one's findings, because those are
the record of what a human was shown when they decided.

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

**The spine is closed and runs end to end**, local-only (Ollama + `LocalStore`).
`repoman run <zip-or-url> --rubric r.md` compiles a rubric, acquires the
submission, probes it, investigates each requirement, resolves every citation and
prints findings; the same run opens in the web UI with its evidence.

Built and tested (`uv run pytest` — 155 tests):

- `core/` — types, and `resolver.py`, the locator verifier (invariant 3)
- `intake/` — clone / unzip / `pypdf`, author emails hashed at acquisition
- `probes/` — all five: `deps`, `tests`, `git`, `injection`, `deploy`
- `verify/` — provider port, the six read-only tools, rubric compiler, verify
  pass, contradiction pass
- `pipeline.py`, `cli.py`, `store/s3.py`, `infra/`
- `web/` — the evaluator workspace, now driven by the real engine

Added on the evening of 2026-09-19, on top of the engine (Track A, while Track B
was away — read these before touching `verify/`):

- **Claims ledger** (`verify/claims.py`, `docs/03` → `Claim`, `Finding.subject`).
  The README/report's own claims are extracted through the read-only tools, each
  pinned to the sentence that made it, and verified like requirements. Claim
  findings share `findings.json` (keyed by the claim id, `subject: "claim"`),
  are never scored and never count toward coverage. Batch toggle `checkClaims`,
  CLI `--no-claims`, run stage `claims`.
- **Cedar policy** (`verify/policy.cedar`, `verify/policy.py`, dependency
  `cedarpy`). `ToolBox` asks the policy for every file, page and probe result.
  **A new tool needs a `permit` line there** or every call is denied. A payload
  on a report page now quarantines the whole PDF.
- **Rubric scales** (`Requirement.scale`: check / points / levels;
  `Decision.level`). The rubric page is one editor: paste-to-compile appends,
  rows can be typed in directly (`sourceSpan: null`), levels seed five classes.
- **AWS audit**: `deploy.sh` and `store/s3.py` checked against the botocore
  service models; the App Runner "waiter" did not exist and is now a status
  poll; `cache_prompt=` → `CacheConfig`.
- **Groq test bench** (`GROQ_API_KEY`): an OpenAI-compatible third provider in
  `verify/model.py` for exercising prompts live without Ollama or Bedrock. Not
  a track.

Not yet done: **nothing has run against Bedrock** — Track 2 is untested, and the
cost figure in `docs/04` is still an estimate. `infra/deploy.sh` has never been
executed. The two public GitHub fixture repos do not exist, so the demo's
permalinks resolve to paths rather than opening on github.com.

Known quality gap: against a local 7B model the locator mismatch rate is high
(most citations on `UNVERIFIED` findings do not resolve). The findings are still
correct — that is the resolver doing its job — but the rate is the metric
`docs/04` says to watch, and it wants a Sonnet baseline before it means anything.
