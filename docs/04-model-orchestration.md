# 04 — Model orchestration

Read this before writing anything that calls Claude. Getting these decisions
wrong is expensive rather than merely incorrect.

All model calls live in `services/verify`. No other module calls Claude.

---

## Model tiering

Three tiers, chosen by what each pass actually requires. A cohort run is not
latency-sensitive, so the bulk passes go through the **Batch API at 50% cost**.

| Pass | Model | $/MTok in · out | Why this tier |
|---|---|---|---|
| Chunk labelling — what does this file do, is this test real | `claude-haiku-4-5` | 1.00 · 5.00 | Thousands of calls, narrow judgment, batched |
| Per-requirement evidence verification | `claude-sonnet-5` | 2.00 · 10.00 | The workhorse; retrieval has already narrowed the field |
| Rubric compilation · contradiction detection · finding prose | `claude-opus-5` | 5.00 · 25.00 | Cross-artifact reasoning, low volume, high stakes |

Use the exact model ID strings above. Do not append date suffixes. Do not
substitute a different tier without measuring — and when you measure, judge
**cost per completed evaluation**, not cost per request. A cheaper model that
needs a second pass is not cheaper.

**Thinking.** Use `thinking: {type: "adaptive"}` on Opus 5 and Sonnet 5. Haiku
4.5 still takes `{type: "enabled", budget_tokens: N}`. Do not use `budget_tokens`
on the 5-series — it returns a 400.

**Effort.** `output_config: {effort: ...}`. Default `high` for the Opus
contradiction pass; `low` or `medium` for the Haiku labelling pass. Tune per
route, not globally.

---

## Rule 1 — Cache the submission, vary the requirement

This maps exactly onto our access pattern and is the single largest cost lever.

Prompt caching is a **prefix match**. The render order is `tools` → `system` →
`messages`, and any byte change anywhere in the prefix invalidates everything
after it.

```
[ tools          ]  ← fixed tool set, sorted, never varies
[ system         ]  ← role, output contract, the compiled rubric
[ repo map       ]  ← file tree, dependency manifest, module summary
[ cache_control breakpoint ]
[ requirement    ]  ← THIS is the only thing that varies
[ retrieved chunks ]
```

One submission checked against twenty criteria pays for its context once. Across
a five-hundred-submission cohort this is the difference between a viable cost and
an unviable one.

**Silent invalidators to avoid in the prefix**: timestamps, per-request IDs,
unsorted JSON keys, a tool list assembled with varying order, anything derived
from `datetime.now()`.

**Verify it works.** Log `usage.cache_read_input_tokens` on every call. If it is
zero across a batch, something volatile crept into the prefix. This check belongs
in the test suite, not in someone's memory.

Note that a mid-conversation top-level `effort` change also invalidates the
messages cache — so set effort per route, once, not dynamically.

---

## Rule 2 — Schema-enforce the citation

Findings come back through structured outputs, not free text.

```python
client.messages.create(
    model="claude-sonnet-5",
    output_config={"format": FINDING_SCHEMA},
    thinking={"type": "adaptive"},
    ...
)
```

The schema requires `evidence` with `minItems: 1`, and each entry must be a valid
`EvidenceLocator`. Use `strict: true` on any tool definitions so arguments
validate exactly, and set `additionalProperties: false` with an explicit
`required` list.

**A finding with no locator is a validation error, not a weak result.** It never
reaches the database. Do not "filter low-confidence findings later" — enforce it
at the boundary where it is cheap and total.

---

## Rule 3 — Resolve every locator before storing it

The model returning a `file_range` is a claim, not a fact. Before storage:

1. Re-read the file from the blob store at the given `blobSha`.
2. Extract `startLine`–`endLine`.
3. Compare against the model's `quote`.
4. On mismatch: drop the evidence, log it, and count it. If a finding loses all
   its evidence this way, the finding is dropped too.

Cheap, deterministic, and it closes the last gap between "the model cited
something" and "the citation is real." Track the mismatch rate as a quality
metric — a rising rate means a prompt or retrieval regression.

---

## Rule 4 — Document citations come from the API, not the model

For the project report, the deck, and any PDF, send it as a `document` content
block with citations enabled:

```python
{
  "type": "document",
  "source": {"type": "file", "file_id": file_id},   # via the Files API
  "citations": {"enabled": True},
}
```

The response splits into multiple `text` blocks; cited blocks carry a `citations`
array with `page_location` (1-indexed pages) or `char_location`. Map those
directly onto `doc_span`.

Two operational notes:

- **Citations are all-or-none per request.** If one document block enables them,
  all must.
- **Citations are incompatible with `output_config.format`** — it returns a 400.
  So the document-grounded pass is a *separate call* from the structured-finding
  pass: extract cited claims first with citations on, then assemble the
  `Finding` in a second structured call that carries the already-located spans.
  Do not try to do both in one request.

Use the Files API (`client.files.upload`) so a report is uploaded once and
referenced by `file_id` across every requirement check for that submission.

---

## Rule 5 — The rubric compiler is an Opus call with a hard honesty requirement

The compiler must return `verifiable: false` with a reason for any criterion that
cannot be checked against the submitted artifacts. Prompt for this explicitly and
test for it: a rubric containing "creativity and originality — 15 marks" must
produce an unverifiable requirement, not a hallucinated check.

The compiled rubric is cached in the stable prefix for the whole submission, so
its output stability matters. Record the prompt hash in the `RunManifest`.

---

## Cost

Envelope estimate with caching and batch pricing: **$0.30–0.70 per submission**,
so roughly $150–350 for a five-hundred-project event.

**This is an estimate from token counts, not a measurement.** Instrument
`response.usage` from the first run — record input, output, cache read, and cache
creation tokens per call into `RunManifest.usage` — and replace this paragraph
with real numbers before anyone quotes it on stage.

---

## Batching

A cohort run is the ideal Batch API workload: high volume, no latency
requirement, 50% discount.

- Submit the chunk-labelling and per-requirement passes as batches.
- Results arrive in **any order** — key by `custom_id`, never by position.
- Poll `processing_status` until `"ended"`, then stream results. Each result has
  `.custom_id` and `.result.type` (`succeeded` / `errored` / `canceled` /
  `expired`); handle all four.
- The Opus contradiction pass is small and interactive — leave it out of batch.

---

## Failure handling

- **Never truncate silently.** If a submission's context exceeds the budget,
  reduce retrieval explicitly and record what was excluded in the finding. An
  evaluator who does not know the tool skipped half the repo cannot evaluate.
- **Check `stop_reason` before reading content**, including `refusal`. A refusal
  on a submission is itself a signal worth surfacing.
- **Catch a chain, not one broad exception class** — `NotFoundError` →
  `RateLimitError` → `APIStatusError` → `APIConnectionError`. Retryable and
  non-retryable failures must be distinguishable.
- **A failed model call produces an `UNVERIFIED` finding** recording the failure,
  never a missing finding.

---

## Provider portability

`ModelClient` resolves the client and the model ID:

| Track | Client | Model ID |
|---|---|---|
| Local / direct | `Anthropic()` | `claude-opus-5` |
| AWS | `AnthropicBedrockMantle(aws_region=...)` | `anthropic.claude-opus-5` |

Both expose the same `messages.create` / `.stream` surface. Everything above this
line in `services/verify` is written once and runs on either.
