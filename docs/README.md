# RepoMan documentation

The full specification. Start with [`00-product-brief.md`](00-product-brief.md)
if you are new; start with [`../AGENTS.md`](../AGENTS.md) if you are an AI coding
agent or about to write code.

| # | Doc | What it covers |
|---|---|---|
| 00 | [Product brief](00-product-brief.md) | Problem, users, thesis, the finding model, the five verification states |
| 01 | [Value and scope](01-value-and-scope.md) | Ranked feature set beyond the core engine; what settles scope arguments |
| 02 | [Architecture](02-architecture.md) | Six-stage pipeline, the single package, the two ports, local vs. cloud, AWS mapping |
| 03 | [Data model](03-data-model.md) | `EvidenceLocator` and every core type. **Source of truth for schemas.** |
| 04 | [Model orchestration](04-model-orchestration.md) | Strands agent, Ollama vs. Bedrock, read-only tools and caps, resolver rule, cost |
| 05 | [Security model](05-security-model.md) | Prompt injection, the four boundaries, sandboxing, PII, data handling |
| 06 | [Build plan](06-build-plan.md) | Two-day phasing, the cut list, the demo script, metrics |
| 07 | [Open questions](07-open-questions.md) | Decisions settled for the hackathon build, and what remains open |

## Reading paths

**"I'm joining the team."**
00 → 01 → 02. Twenty minutes.

**"I'm implementing a feature."**
[`../AGENTS.md`](../AGENTS.md) → 03 → the doc for your layer.

**"I'm writing something in `verify/`."**
04, all of it, before the first line.

**"I'm touching submitted content or running submitted code."**
05, all of it. No exceptions, including for quick local tests.

**"We're arguing about whether to build X."**
01 and the cut list in 06.

## Conventions for editing these docs

- 03 changes first, then `repoman/core/types.py`, in the same commit.
- When an open question in 07 is settled, move the answer into the doc it affects
  and leave a one-line record in 07 saying when and why.
- These docs are the context AI agents load. Keep them concrete — names, types,
  exact model IDs, real numbers — and keep prose that does not change behavior out
  of them.
