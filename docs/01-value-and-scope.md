# 01 — Value and scope

The claim-to-evidence engine is the product. This document covers everything
around it that decides whether anyone can actually run RepoMan on Monday
morning, ranked by value per unit of build effort.

Use this to settle scope arguments. If something is not on this list and not in
[`06-build-plan.md`](06-build-plan.md), it is not in scope yet.

---

## 1. Intake is the integration story

Nobody hand-assembles a submission. RepoMan currently starts at "an evaluator
provides a submission," which is not how any real workflow behaves. The adapter
layer is what makes it deployable rather than demoable.

| Adapter | Why it matters | Effort |
|---|---|---|
| **Google Sheet + Drive folder** | Unglamorous; covers most university and small-event workflows. Ship first. | Low |
| **Zip / local folder** | The CLI path and the test fixture path. | Low |
| **GitHub App / Classroom** | Install on an org, auto-discover every repo for an assignment. Commit history, PR activity and contributor stats come free. Highest leverage for the faculty user. | Medium |
| **Hackathon platforms** | Devpost, Devfolio, DoraHacks, Unstop all export CSV. One importer covers hundreds of submissions. | Low–Medium |
| **LMS via LTI 1.3** | Canvas, Moodle, Google Classroom. Matters most as the *output* path — scores and feedback return to the gradebook. Nobody wants a second grading system. | High |

LTI is the highest adoption value and the highest cost. It is deliberately cut
from v1; see [`06-build-plan.md`](06-build-plan.md).

---

## 2. Contribution forensics

Git history answers questions no rubric thought to ask, and all of it is
deterministic — no model call, no hallucination surface, high evaluator trust.

- **Commit timeline against the event window.** How much of this predates the
  hackathon? A project pushed in one burst before the start is a different
  submission than one built during it.
- **Fork and reskin detection.** Compare against upstream; look for orphaned
  initial commits and single large code-dump commits. *(Built: `probes/fork.py`.
  A shallow clone's oldest commit only looks like a root, so the probe says the
  history was truncated rather than reading a depth limit as "this was copied".)*
- **Contribution distribution** across team members — one person wrote
  everything, or a fair split?
- **Cross-submission similarity** across the cohort. Among eighty projects,
  "these four share an identical service layer" is plagiarism/collusion
  detection that no existing tool gives a professor. *(Built:
  `probes/similarity.py`. Matching is by normalised content hash, so it catches
  renamed files and reformatted copies; it does **not** catch renamed variables,
  and the UI says identical files can equally mean a shared template or the same
  tutorial.)*

For many evaluators this feature alone justifies installing RepoMan. It is also
cheap: it is all `git log`, tree hashing, and similarity comparison.

---

## 3. The rubric is a compiled artifact

Evaluators paste prose: *"good software engineering practices — 20 marks."*
Treating that as a prompt input is the difference between a toy and a tool.

The rubric compiler:

- decomposes prose into individually checkable requirements,
- assigns each a weight and a source span back into the original rubric text,
- and **flags the criteria it cannot check** — "creativity is not
  evidence-verifiable; this criterion stays 100% human."

The compiled checklist is editable before the run, reusable across a department,
and versioned per semester. It is also what makes every downstream finding
explainable: each finding points at a compiled requirement, which points at a
line in the evaluator's own rubric.

---

## 4. "Does it actually run?"

Static evidence is half a claim. Deterministic probes buy enormous credibility
for very little work:

- **Build and test in a sandbox.** Does it compile? Do tests pass? What is real
  coverage versus claimed coverage? *(Cut from v1 — see
  [`06-build-plan.md`](06-build-plan.md). Test discovery and dependency
  reachability are static and stay in.)*
- **Hit the deployed URL.** Alive, status code, screenshot of the landing page,
  captured with a timestamp.
- **Dependency reachability.** `redis` in the manifest with zero imports is
  exactly the "declared but unused" finding — computed, not inferred.

Mixing deterministic checks with model reasoning also materially improves trust
in the model-produced findings, because the evaluator can see that some of the
system is not guessing at all.

---

## 5. Calibration across evaluators

"More consistent" needs a mechanism behind it. In build order:

1. **Drift detection** — evaluator A runs 1.5 points above evaluator B on the
   same criterion across the same cohort.
2. **Similar-submission comparison** — "you gave this 7; three projects with
   identical evidence coverage got 4–5."
3. **Blind mode** — hide names, institutions and README polish so presentation
   quality does not leak into technical scoring. Stripping happens *before
   retrieval*, not at render time.
4. **Anchor set** — every judge scores the same three projects first to calibrate
   the panel.

For a thirty-judge hackathon this is the difference between a result and a
lottery. It needs real multi-evaluator data to be anything but a mock, so it is
post-v1.

---

## 6. Triage before evaluation

At five hundred submissions the first question is not "what is the score," it is
"what do I look at." The queue sorts by **where human judgment is actually
needed**: clear passes and clear incompletes move fast; contested, ambiguous and
flagged submissions surface first.

The headline metric is **time to decision**, not accuracy against some imagined
ground truth. Instrument it.

---

## 7. Three artifacts come out, not one

- **Evidence packet** — permalinked, immutable, shareable. This is what survives
  an appeal.
- **Student-facing feedback report** — evidence-based feedback is far more useful
  than a number, and it is the thing that gets RepoMan adopted by teaching staff
  who currently write this by hand.
- **Cohort report** — "62% of the class never implemented authorization" is real
  teaching signal that no existing tool produces.

---

## 8. Overrides become precedent

When a human overrides a finding — "we accept `.env.example` as evidence of
config management" — store it as **rubric precedent** and apply it to the
remaining submissions in the batch.

Two effects, both real: the tool visibly improves over a grading session, and
every remaining submission is held to the same standard. The second is a fairness
property, not just a demo beat.

---

## 9. Surfaces beyond the web workspace

- **CLI** (`repoman run ./submission --rubric rubric.md`) — this is what makes
  "student data never leaves the machine" a credible claim rather than a slide.
  With Ollama as the model it is literally true. It matters for FERPA-style
  institutional procurement.
- **Student self-check** — the same CLI, run by the submitter before submitting,
  against the published rubric. No new code; a second user for free, and
  submissions arrive with fewer unverifiable claims.
- **Viva questions** — every non-`VERIFIED` finding carries two or three
  questions to ask the team. For oral exams and hackathon Q&A rounds this is the
  first thing evaluators reach for.
- **Browser extension** — overlay the requirement/evidence panel on github.com
  while the evaluator is already reading the repo there.

---

## Framing notes

**On scoring.** Hold the line on not producing a number. Do produce **evidence
coverage** — "8 of 12 requirements have verified evidence" — which is objective,
defensible, and sortable, without claiming to judge quality.

**On the demo.** Run RepoMan on RepoMan's own submission, live. It is immediately
legible to any audience and it forces honesty about our own unverified claims,
which is precisely the argument the product makes.

**On prompt injection.** The defense described in
[`05-security-model.md`](05-security-model.md) is both a genuine security
property and the most memorable ninety seconds of any demo. Plant a payload in
the fixture repo and show it being caught.
