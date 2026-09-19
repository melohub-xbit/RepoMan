# 06 — Build plan

Two days, one developer, Claude Code doing the typing. The vertical slice comes
first: **one submission, one rubric, one finding rendered with a working
permalink.** Everything after that is breadth on a spine that already runs.

Resist building any layer wide before the spine is closed. A workspace with no
locator resolution is a demo; a terminal printout with a permalink that opens is
a product.

---

## Before Day 1 (thirty minutes, do it tonight)

- [ ] Bedrock → Model access → enable Claude Sonnet in your region. This can take
      hours to approve; nothing else is blocked on it but the demo is.
- [ ] Billing → Credits → confirm Amazon Bedrock is an applicable product.
- [ ] `ollama pull qwen3:8b` so Track 1 is testable offline.
- [ ] Create the S3 bucket and an ECR repo. Nothing else in AWS yet.

---

## Day 1 — Spine (local only, no UI)

Goal: `repoman run ./fixtures/springboot --rubric fixtures/rubric.md` prints
findings, and the `file_range` permalink for the `CONTRADICTED` one opens to the
right lines on GitHub.

Build order — each step is testable before the next starts:

1. [ ] `core/`: pydantic types from [`03-data-model.md`](03-data-model.md),
       `EvidenceLocator`, finding states, `RunManifest`.
2. [ ] `core/resolver.py` — re-reads the checkout, matches the quote, drops on
       mismatch. **Before anything produces locators.** Unit test with a
       hand-written good and bad locator.
3. [ ] `store/`: `Store` protocol, `LocalStore`. `S3Store` is Day 2.
4. [ ] `intake/`: GitHub URL → shallow clone at HEAD, record commit SHA;
       zip → extract; README located. `pypdf` for `--report`.
5. [ ] `verify/model.py` + one Strands agent with `tree`/`grep`/`read_file`.
       Smoke-test against Ollama first (free, fast), then Bedrock.
6. [ ] Rubric compiler with `verifiable: false` working on a "creativity" line.
7. [ ] Verify pass with `structured_output(FindingDraft)`, `min_length=1`, then
       resolver, then `findings.json`.
8. [ ] `cli.py` prints findings as a table with permalinks.

**Exit criterion:** the deliberately false claim in `fixtures/springboot/README.md`
produces `CONTRADICTED`, and the cited permalink opens to the right lines.

---

## Day 2 morning — Credibility

Goal: findings stop looking like a language model and start looking like an
investigation.

- [ ] Probes: `deps` (declared vs imported), `tests` (test files, framework,
      count), `git` (timeline vs event window, per-author share, single-dump
      detection), `injection` (imperative-to-evaluator regexes, zero-width and
      hidden text), `deploy` (GET, status, title, timestamp). Each is one pure
      function with a fixture test.
- [ ] `FLAGGED` on `fixtures/injected/`; quarantined artifact excluded from tools.
- [ ] Contradiction pass across a submission's findings.
- [ ] Viva questions: `Finding.questions` populated for non-`VERIFIED` findings.

**Exit criterion:** a finding cites code and the report page at once, both links
land, and the injected fixture is flagged with the payload's locator.

## Day 2 afternoon — Ship it

- [ ] `web/`: batch list → submission page with evidence cards (permalink,
      quote, provenance badge, state) → override form (accept / override + note
      + score) → precedent applied to later runs in the batch → CSV and
      Markdown export. Server-rendered; no JS framework.
- [ ] `S3Store`. Dockerfile. `deploy.sh`: build, push to ECR, create/update the
      App Runner service with env vars and the instance role.
- [ ] Evidence coverage on the batch list. Time-to-decision timer on the
      submission page.
- [ ] Run RepoMan on RepoMan. Fix whatever that exposes.
- [ ] Rehearse the demo twice, once against Bedrock, once against Ollama.

**Exit criterion:** a public App Runner URL; a five-submission batch completes;
`manifest.usage` gives a real cost per submission.

---

## Deliberately cut from v1

Recorded so they are not re-argued mid-build.

| Cut | Why |
|---|---|
| **Sandboxed build and test** | The strongest probe, but Docker-in-App-Runner is a day by itself. v1 executes no submitted code at all, which is also the simplest possible security posture. `deploy` liveness gives a cheap "does it run" signal. |
| **Video transcription** | Expensive, low signal per token. Deck-as-PDF covers most of the demo-evidence value. |
| **Retrieval index** | Replaced by the agent's search tools. See [`02-architecture.md`](02-architecture.md). |
| **DynamoDB / Lambda / Step Functions / EventBridge / Cognito / Amplify** | Each is a good next step and none is needed to show the thesis. |
| **Model tiering, Batch API** | Batch API is not on Bedrock at all. One model, measured first. |
| **LTI, browser extension, calibration, cross-institution similarity** | Post-hackathon, as before. |
| ~~**Cross-submission similarity**~~ | **Built after all** — `probes/similarity.py`. Normalised per-file hashes written per run; the pairing is computed from the batch on demand, so a later submission never rewrites an earlier one's findings. Catches renamed and reformatted copies, not renamed *variables*. |
| **Anything producing a number** | Permanent. Holding this line *is* the demo. |

---

## The demo (four minutes)

Open with the personal line — you have been on both sides of this: graded by
someone who never opened the repo, and judging while skimming. Then:

1. Submission URL and rubric pasted in; compiled requirements shown, one marked
   *not verifiable — stays human*. Run. Thirty seconds.
2. One `CONTRADICTED` finding: click the permalink, GitHub opens at the lines;
   click the report page, the quote is highlighted. "The report says five roles.
   The code has two."
3. The planted injection payload, caught and flagged, with its own locator.
   "Every submission is hostile input. It never reached the model as an
   instruction."
4. One `UNVERIFIED` finding, explained as a feature: "We looked here, here and
   here and found nothing. That is an answer, not a gap."
5. Override it; the precedent applies to the rest of the batch. Show the batch
   list with evidence coverage. Point at the Track 1 terminal running the same
   thing on Ollama with no AWS account.

Close on the sentence: RepoMan does not judge people's work. It does the
tedious investigation required before a human can judge it well.

---

## Metrics to instrument from day one

- **Time to decision** per submission — the headline metric, not accuracy.
- **Evidence coverage** distribution across a batch.
- **Locator mismatch rate** — a rising rate means a prompt regression.
- **Cost per completed evaluation** from `manifest.usage`.
- **Override rate** per requirement — a criterion overridden every time is a
  rubric compiler bug.
