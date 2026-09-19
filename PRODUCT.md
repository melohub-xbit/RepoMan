# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Decided before init: Python, FastAPI + Jinja2 server-rendered, HTMX for partial updates,
native View Transitions for page changes. No JS framework, no build step. Deployed as one
container on AWS App Runner; local track runs the same app against Ollama and local disk.

## Users

Primary (the demo scene): a **hackathon judge** with a laptop at a venue, thirty
submissions in about three hours. Short bursts of attention, noisy room, needs to triage
fast and reach a defensible decision per project.

Confirmed second audience (the pitch, not the demo): professors and TAs grading a cohort
over a week; hiring reviewers on take-home assignments. Same workflow at a slower cadence.

The submitter (student/team) is a confirmed later audience via the CLI self-check; not a
surface in v1.

## Product Purpose

RepoMan takes a submission (GitHub repo or zip, README, report/deck as PDF, deployed URL)
and the evaluator's rubric, and produces **Requirement → Evidence → Finding** records —
each citing an exact location a human can open — so the evaluator spends their time
judging instead of hunting. Success is **time to a defensible decision** per submission,
and a record a student could appeal.

## Positioning

RepoMan never produces a score, a grade, a rank, or a suggested band. Its mechanism is
**claim-to-evidence verification**: every finding carries a locator that was re-read and
matched against the pinned checkout before it was stored. `UNVERIFIED` ("we looked here
and found nothing") is a first-class answer. A neighbouring "AI grader" cannot truthfully
copy this without giving up the grade.

## Operating Context

- The evaluator's task per submission: read compiled requirements, open each finding's
  evidence in place (code lines, report page), accept or override, enter their own marks,
  move on. A batch queue orders submissions by where a human is needed most.
- Rubrics arrive as prose or PDF and are compiled into an editable checklist the
  evaluator approves before any run.
- Finding states: `VERIFIED`, `PARTIAL`, `UNVERIFIED`, `CONTRADICTED`; orthogonal flags
  (`PROMPT_INJECTION`, `TIMELINE_ANOMALY`, `CONTRIBUTION_SKEW`, …). Evidence carries
  provenance: `probe` (deterministic) or `model`.
- Exports: CSV for a gradebook, a student-facing feedback report, an evidence packet.
- Terminology is fixed in `AGENTS.md`: Submission, Artifact, Requirement, Evidence,
  Finding, Decision, Precedent. These words appear in the UI.

## Capabilities and Constraints

- Built in two days by two people for an AWS hackathon; judged on solving a real problem
  and on being "the best-designed thing at the event — a pleasure to use".
- The one number the product shows is **evidence coverage**, as a count ("3 of 5"),
  never a percentage, never weighted, never coloured like a grade.
- All submitted content is hostile input; the UI shows what was quarantined and why.
- Video, sandboxed execution, LMS integration, multi-evaluator calibration: out of v1.
- Undecided: whether the student-facing report is sent by RepoMan or forwarded by the
  evaluator after editing (docs/07 Q2). Do not design a "send to student" action.

## Brand Commitments

- Name **RepoMan** is final. No logo, no colours, no type decided.
- Voice: plain, specific, evidentiary. Findings are written like an investigator's note,
  not a verdict and not marketing.

## Evidence on Hand

- A complete specification in `docs/` including a worked example finding (Spring Boot
  RBAC: two roles in code, report claims five) — real demonstration material.
- Fixture repositories are being created for the demo; no real student data exists and
  none may be fabricated as such.
- No testimonials, customers, or benchmarks. Do not invent any.

## Product Principles

1. The human judges; RepoMan is the clerk who pulled the files. Nothing on screen may
   read as a verdict before the human enters one.
2. Every claim on screen is one click from its evidence, and the evidence opens in place.
3. Absence of evidence is information, shown as calmly as presence.
4. Fast triage first, depth on demand: the queue tells you where to look; the workspace
   lets you look.
5. Not a dashboard, not an AI product: no KPI tiles, charts, glow, gradients, sparkles,
   or chat metaphors. The confirmed anti-references.

## Accessibility & Inclusion

State is never carried by colour alone (label and shape always accompany it). Full
keyboard operation of a grading pass. Light and dark both supported. (Inferred, not
user-stated: the pitch is projected at a venue, so the light rendition is the one
rehearsed.)
