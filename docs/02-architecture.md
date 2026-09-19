# 02 — Architecture

Built for a two-day hackathon with one developer and $100 of AWS credits. Every
choice below is the simplest thing that satisfies the invariants in
[`../AGENTS.md`](../AGENTS.md) and lands on the hackathon's own service list.
Where the earlier plan was heavier (two languages, generated type mirrors,
Aurora, Fargate, SQS, a retrieval index), that is recorded in
[`07-open-questions.md`](07-open-questions.md) as settled, not re-argued here.

## Pipeline

Six stages, strictly ordered. Each stage's output is the next stage's only
input, which is what makes a run replayable from its manifest.

```
01 Intake          GitHub URL or zip (+ optional report PDF, deploy URL) → Submission + Artifact[]
02 Acquire         git clone at a pinned commit; PDF → per-page text; stored under the run
03 Compile rubric  Prose → Requirement[]; unverifiable criteria marked, evaluator edits before run
04 Probe           Deterministic checks: deps, tests, git timeline, fork/reskin, injection scan, deploy liveness
05 Verify          One agent run per requirement: read-only tools over the checkout → Finding
06 Human decides   Workspace, overrides, precedent, export
```

**There is no index stage.** The earlier plan chunked and embedded the
repository, then retrieved per requirement. Instead the verify agent *searches*
the checkout the way a TA would — `tree`, `grep`, `read_file`, `read_report_page`
— and cites what it finds. This removes tree-sitter, embeddings, a vector store
and a retrieval-tuning loop, and it maps directly onto the Strands Agents SDK on
both tracks. The cost is more model tokens per requirement; the tool-call cap in
[`04-model-orchestration.md`](04-model-orchestration.md) bounds it.

**Replayability.** Every run writes a `RunManifest` recording the commit SHA, the
compiled rubric version, the probe versions, the model ID and the prompt
template hashes. Given a manifest, a run can be reproduced or diffed.

## One package, one language

Python 3.12, one installable package. The earlier TypeScript/Python split with
generated type mirrors was correct for a team and wrong for two days: every hour
spent on schema codegen is an hour without a finding. Python owns everything;
the UI is server-rendered templates in the same process.

```
repoman/
  core/        types (pydantic), EvidenceLocator, resolver, finding states, RunManifest. No I/O.
  intake/      GitHub URL / zip → Submission. git clone, PDF → pages.
  probes/      Pure functions: deps, tests, git, fork, injection, deploy, similarity. No model calls.
  verify/      Rubric compiler, the Strands agent + tools, finding assembly. The only module that talks to a model.
  store/       The one port: Store(put_json/get_json/list/put_bytes/get_bytes). LocalStore | S3Store.
  web/         FastAPI + Jinja2: batch list, submission findings, evidence cards, override, export.
  cli.py       repoman run <repo-or-zip> --rubric r.md [--report r.pdf] [--deploy URL]
fixtures/      Small real repos: springboot/ (false claim in README), injected/ (planted payload)
infra/         Dockerfile, apprunner.yaml, deploy.sh
```

### Dependency rules

- `core` imports nothing outside the stdlib and pydantic.
- Nothing imports `verify` except `cli.py` and `web/`. Model calls do not leak.
- `probes` may not import `verify`. Determinism is the point, and it is also a
  security control — see [`05-security-model.md`](05-security-model.md).
- Every tool the agent can call is read-only over the checkout. There is no tool
  that clones, fetches, writes, or executes.

## Local-first and cloud are the same code

Two things differ between the tracks, and both are resolved from environment
variables at startup. Nothing else in the codebase knows which track it is on.

| Concern | Track 1 — local, no AWS account | Track 2 — deployed on AWS |
|---|---|---|
| `Store` | `LocalStore(./data)` | `S3Store(bucket)` |
| Model | Strands `OllamaModel` (`qwen3:8b` default) | Strands `BedrockModel` (Claude Sonnet) |

```python
# repoman/verify/model.py — the whole port
def make_model():
    if os.environ.get("REPOMAN_OLLAMA_HOST"):
        return OllamaModel(host=..., model_id=os.environ.get("REPOMAN_MODEL_ID", "qwen3:8b"))
    return BedrockModel(model_id=os.environ["REPOMAN_MODEL_ID"], region_name=...)
```

**Never write `if is_cloud` in business logic.** If a behaviour differs between
tracks it belongs in `store/` or `verify/model.py`; if it fits neither, the port
set is wrong — raise it rather than adding a conditional.

### Why Strands on both tracks

The hackathon lists Strands Agents SDK for Track 1 and Bedrock for Track 2.
Strands ships both model providers behind one `Agent` API, gives `@tool` for the
read-only tools, and `structured_output()` for schema-enforced findings. One
agent implementation, one line of difference. Local models are weaker at tool
use than Sonnet; the Track 1 demo uses a rubric with concrete criteria and the
Track 2 demo carries the batch story.

## Storage

Everything is JSON under a run prefix in the `Store`. There is no database.

```
batches/<batchId>.json
runs/<runId>/manifest.json
runs/<runId>/submission.json
runs/<runId>/rubric.json
runs/<runId>/probes.json
runs/<runId>/findings.json
runs/<runId>/decisions.json      # human writes; everything else is machine output
runs/<runId>/report_pages.json
runs/<runId>/fingerprint.json    # normalised per-file hashes, for cross-submission comparison
runs/<runId>/repo/               # the checkout, only in LocalStore; S3Store keeps a tarball
precedents/<batchId>.json
```

Cross-submission similarity has no file of its own on purpose. It is derived from
the batch's `fingerprint.json` files whenever it is displayed, because its answer
changes as the batch fills and a later submission must never rewrite an earlier
one's findings — those are the record of what a human was shown when they decided.

`# ponytail: decisions.json is read-modify-write with no lock. Fine for one
evaluator per batch; move Decision/Precedent to a DynamoDB table when two people
grade the same batch concurrently.`

Content addressing survives in the form that matters: every locator carries the
`commitSha` of the checkout, so permalinks stay valid after a force-push, and the
`Evidence.quote` is denormalized so exports render after the repo goes private.

## Concurrency

- One process. The web server runs a submission's pipeline in a background
  thread; the CLI runs it inline.
- Stage 05 fans out per requirement through a `ThreadPoolExecutor(max_workers=4)`.
  Bedrock throttles above that on a fresh account.
- A failed probe or model call produces an `UNVERIFIED` finding with the failure
  recorded, never a missing finding (invariant 4).

`# ponytail: in-process thread pool. Move stage 05 to Lambda behind a Step
Functions Map state when a batch outgrows one App Runner instance.`

## Deployment (Track 2)

| Concern | Service | Why this one |
|---|---|---|
| App + UI + pipeline | **App Runner**, one container from ECR | "A URL in minutes" is literally the track description. One Dockerfile, no VPC, no Lambda layers for `git`. |
| Blob + JSON store | **S3**, versioning on | The only persistence. |
| Model | **Amazon Bedrock**, Claude Sonnet | Frontier model billed to the AWS account — covered by credits. |
| Auth | Single shared token (`REPOMAN_TOKEN`), HTTP basic | Cognito is the post-hackathon item. |
| Secrets | App Runner env vars; task role grants `s3:*` on one bucket and `bedrock:InvokeModel*` | Nothing else. |

Not used in v1, deliberately: Lambda, API Gateway, DynamoDB, Step Functions,
EventBridge, Cognito, Amplify. Each is a natural next step (noted above where it
applies) and none is needed to demonstrate the thesis.

## Why this shape

1. **Every finding is traceable to bytes.** The resolver re-reads the checkout at
   the pinned commit and matches the quote before a locator is stored.
2. **Deterministic and model-derived evidence are interchangeable downstream.**
   Same `Evidence` type, `provenance` field tells the UI which is which.
3. **The human is a pipeline stage, not a consumer.** `Decision` and `Precedent`
   are first-class and feed back into later runs in the batch.
