# 00 — Product brief

## The problem

Evaluators are asked to make decisions about complex work by manually piecing
together information from multiple sources. A professor grading a software
project inspects a GitHub repository, reads a report, watches a demo, checks
whether requirements were actually implemented, and compares everything against a
rubric. A hackathon judge does the same across dozens or hundreds of structurally
different projects.

The hard part is not assigning marks. It is **understanding what was actually
built, verifying the submitter's claims, and finding enough evidence to make a
fair decision**. That work is currently manual and fragmented, and it scales
badly: ten projects is manageable, eighty is a weekend, five hundred is a
lottery.

There is also a quality problem independent of volume. A requirement buried in a
large repository or a long report gets missed. Evaluators spend most of their
time *finding* information rather than judging it.

## The product

RepoMan accepts a submission and a rubric and produces a structured evaluation
workspace. It identifies what the project claims to implement, maps those claims
to concrete evidence in the repository and demonstration, checks requirements
against the actual implementation, identifies missing or unverified
functionality, and highlights inconsistencies between the report, the demo, and
the code.

It automates the **investigation and evidence-gathering layer** while keeping the
judgment with the human. This is deliberate: evaluation involves context and
qualitative assessment that should not be delegated to a model. The goal is to
make the evaluator faster, better informed, and more consistent — not to pretend
a model can determine the value of someone's work.

## The thesis

> RepoMan does not judge people's work. It does the tedious investigation
> required before a human can judge it well.

## Users

The initial user is a **college professor or TA evaluating software projects** —
a concrete, recurring use case with a clear workflow.

The same platform serves hackathon judges, research supervisors, internship
reviewers, and hiring teams reviewing take-home projects. All of them have the
same underlying workflow: receive a submission, hold a set of criteria,
understand the work, make a human judgment.

This is why RepoMan is not "a hackathon judging tool." It is an **evidence-based
evaluation platform for complex work**. A professor evaluates eighty final-year
projects, an organizer processes hundreds of hackathon submissions, a company
reviews engineering assignments — same system, different rubrics.

## What makes it different

The core capability is **claim-to-evidence verification**, not summarization and
not grading. Instead of describing what a project *appears* to do, RepoMan shows
where the evidence for each claim exists and where it is missing.

## The unit of output

RepoMan's atomic output is a **requirement mapped to located evidence with an
explicit verification state**. Everything else in the system exists to produce,
store, display, or export one of these.

```
Requirement   Role-based authorization
              Security & access control — criterion 3 of 5 · weight 8

Code          SecurityConfig.java:41-68
              UserRole.java:7-12
              OrderController.java:29 @PreAuthorize

Demo          demo.mp4 02:14-02:38
              "...and admins get the extra tab here"

Report        report.pdf p.9 ch.4102-4190
              "full RBAC across all five user tiers"

Finding       Two roles defined (ADMIN, USER); the report claims five. Three of
              nine controllers carry an authorization annotation — the rest are
              reachable by any authenticated caller.

State         PARTIAL + CONTRADICTED
Confidence    High — three independent probes agree; the report is the outlier.
```

The evaluator scores this. RepoMan's job ended at the last line.

## The five verification states

Most AI grading tools collapse to pass/fail because a binary is easier to prompt
for. The interesting information lives in the middle.

| State | Meaning | Evaluator action |
|---|---|---|
| `VERIFIED` | Evidence found and cross-checked | Skim it and move on |
| `PARTIAL` | Evidence exists but is narrower than the claim | Needs a human eye |
| `UNVERIFIED` | We looked and found nothing | Decide whether absence matters |
| `CONTRADICTED` | Two sources disagree | Highest-value finding in the system |
| `FLAGGED` | Integrity signal — injection, timeline anomaly, cross-submission overlap | Investigate before scoring |

`UNVERIFIED` is not a failure mode, it is a product feature. It is distinct from
"not implemented" and must be worded that way everywhere it appears. An evaluator
who learns that `UNVERIFIED` genuinely means "we could not find it" will trust
`VERIFIED`. One bluffed citation destroys that for the whole session.

`FLAGGED` is orthogonal to the other four and can co-occur with any of them.

## Why it fits a hackathon build

The system combines an investigating agent (Strands Agents SDK), deterministic
code and git analysis, document reading, and verified evidence linking. It has a
genuine local-first version — Ollama, local disk, no AWS account — where
sensitive student or company submissions never leave the evaluator's machine,
and a cloud version on Bedrock, S3 and App Runner where a cohort is processed
from one URL. The two tracks are the same code with two environment variables
changed — see [`02-architecture.md`](02-architecture.md).

## The defensibility argument

Anyone can point a model at a repository. The moat is the **audit trail**: an
evaluation that a student can appeal and an institution can defend. Every design
decision in this repository should be checked against that sentence.
