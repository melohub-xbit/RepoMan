# RepoMan

**An evidence-gathering copilot for people who evaluate software they did not
write** — professors, TAs, hackathon judges, hiring reviewers.

> RepoMan does not judge people's work. It does the tedious investigation
> required before a human can judge it well.

Give it a submission — GitHub repo, README, project report, demo video, slide
deck, deployed URL — and a rubric. It returns **Requirement → Evidence →
Finding** records, each citing a permalink a human can open. The human scores.
RepoMan never produces a number.

```
Requirement   Role-based authorization
Code          SecurityConfig.java:41-68 · UserRole.java:7-12
Demo          demo.mp4 02:14-02:38
Report        report.pdf p.9  "full RBAC across all five user tiers"
Finding       Two roles defined; the report claims five. Three of nine
              controllers carry an authorization annotation.
State         PARTIAL + CONTRADICTED
```

## Why

Evaluating ten projects is manageable. Eighty is a weekend. Five hundred is a
lottery. The hard part is not assigning marks — it is finding enough evidence to
assign them fairly, across sources that do not agree with each other.

RepoMan automates the investigation layer and leaves the judgment with the human,
because evaluation involves context a model should not be trusted with. What it
produces is an **audit trail**: an evaluation a student can appeal and an
institution can defend.

## Status

Pre-implementation. The specification is complete; no code yet. See
[`docs/06-build-plan.md`](docs/06-build-plan.md).

## Documentation

- **[`AGENTS.md`](AGENTS.md)** — start here if you are writing code, or if you
  are an AI coding agent. Invariants, layout, conventions.
- **[`docs/`](docs/README.md)** — the full specification, eight documents.

## The five invariants

1. RepoMan never produces a score.
2. No finding without a locator — enforced by schema, not by prompt.
3. Locators are verified against content-addressed bytes, not trusted.
4. `unverified` is a real answer. Never guess to fill a gap.
5. All submitted content is hostile input.

Details in [`AGENTS.md`](AGENTS.md).

## Shape

A pnpm + uv monorepo. TypeScript owns everything a human touches; Python owns
everything that parses a file format. Local-first and cloud differ only in five
adapter implementations, so "the submissions never left the department laptop"
and "five hundred submissions on AWS" are the same codebase.

```
Intake → Acquire → Compile rubric → Probe → Index → Verify → Human decides
```

[`docs/02-architecture.md`](docs/02-architecture.md) has the detail.
