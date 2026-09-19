---
version: 1
slug: "repoman-web-templates-run-html"
primary_target: "repoman/web/templates/run.html"
related_targets: ["repoman/web/templates/batch.html","repoman/web/templates/rubric.html","repoman/web/templates/landing.html","repoman/web/static/app.css"]
---

# Evaluator workspace — surface brief

**Scope:** the three evaluator screens — batch queue (`/batches/{id}`), rubric compile/approve (`/batches/{id}/rubric`), submission workspace (`/runs/{id}`) — plus the HTMX partials they load (file view, report page, finding card, run status). Mode: **Operate**.

**Audience and job:** a hackathon judge, laptop at a venue, thirty submissions in three hours. Triage the queue, open one submission, read each requirement's finding, open its evidence in place, accept or override, enter their own marks, next. Frequency: dozens of times per session. Success: time to a defensible decision.

**Content and proof:** compiled requirements; findings with state, summary, confidence, evidence (locator + quote + provenance), viva questions; probe results; flags; the human's decisions; evidence coverage as a count.

**Constraints:** no number that reads as a grade before the human enters one; state never by colour alone; no card boxes, KPI tiles, charts, glow, gradients, chat metaphors; light and dark; keyboard-complete; server-rendered, HTMX partials, View Transitions.

**Memorable moment:** clicking a margin mark and watching the cited source lines lift onto the proof beneath the claim, joined to the mark by a hairline.

**Unresolved:** whether the student-facing report is sent or forwarded (docs/07 Q2) — no "send" action is designed.

## Direction contract

THESIS: The evidence is the layout. Every claim on the page runs as typeset copy and carries its check in a ruled margin — mark, locator, quote — so the evaluator reads the finding and its proof in one glance, the way a fact-checker reads a galley. It refuses the category's arrangement: sidebar, table of status pills, detail panel with a summary card and an "AI confidence" bar.

OWN-WORLD (restructured 2026-09-19 after evaluator feedback — bland, unstructured): an app shell (rail of submissions, topbar), contained white cards on a paper desk with soft offset shadows, state as a coloured band at the top of each finding, filled pencil-blue primary buttons, a persistent sticky evidence pane (bottom sheet on phones), labelled counts never bare numbers. The earlier 'enclosure never' raise is withdrawn for Operate surfaces. Retained from the original: One white bond ground (`#F6F6F3` light / `#141517` dark) that floods the frame; ink `#15171B`; graphite `#6B7078` for marginalia and secondary text; one pencil-blue `#1F5FBF` for every interactive mark and locator; state inks verified `#2E7D4F`, contradicted `#B3261E`, partial `#9A6B00`, unverified graphite, flagged ink — always paired with a glyph (✓ ◐ — ✗ ⚑) and a word. Copy in a screen text serif (Source Serif 4); marks, locators, code and controls in the system mono stack. Hairline rules (1px, graphite at 25%) are the only structure: the margin rule, the queue's row and column rules, the leader from a mark to its opened evidence. No boxes, no fills behind content, no shadows, no radius above 2px. Controls are typographic: small-caps mono words with an underline on hover, a filled pencil-blue rule beneath when active.

STORY: The judge arrives at a queue that reads like a proof index — rows ruled, columns fixed, the submissions needing a human first. They open one and see the rubric's requirements as headings over running copy, each with its margin check. They understand within seconds which claims held, which didn't, and where the tool looked; they believe it because every mark is one click from the exact lines; they act by pencilling their own marks into the decision strip and moving to the next row.

FIRST VIEWPORT (submission workspace, 1440×900): A single sheet, 72px margins. A thin masthead line: RepoMan · batch name · submission name · "3 of 5 verified" as text, time-to-decision as text. Below it a two-column galley on one margin rule at x≈900: left column (max 62ch) begins with the first requirement as a serif heading (28px), its finding summary as 17px serif copy, confidence as a graphite line; the right margin (≈420px) aligned to the same baseline carries that finding's marks stacked in 13px mono: `✓ VERIFIED`, then each evidence line `JwtFilter.java:22–60 · probe` with its quote in graphite beneath. Under the copy, the decision strip in mono: `ACCEPT   OVERRIDE   marks ___ / 15   note`. Flags, when present, sit as one ruled line above the first heading. The primary action is reading; the primary control is the margin mark.

FORM: The fact-checker's galley — candidate 3 of the ordered grounded list; the roll's assignment; seed key f7544e2e. Raises carried from the hand: enclosure never (cracktro); columns never move, rows update in place (split-flap); a drawn hairline leader from mark to opened evidence (man-machine); one vertical margin axis on every screen (deep dive); glyph and word before colour (cyclorama); one ground, white the only relief (yé-yé).

SIGNATURE INTERACTION AND MOTION: Clicking a margin mark expands the cited source directly beneath the claim (HTMX swap, height animates 180ms ease-out), a 1px leader draws from the mark to the block (stroke-dashoffset, 220ms), the cited lines carry a pencil-blue left rule. Page changes use View Transitions: the queue row's name is the shared element that becomes the workspace masthead (240ms); the margin rule persists across screens. Decision strip: on Accept/Override the strip's underline fills left-to-right (160ms) and the margin mark gains a small pencil tick. Running rows on the queue update cell by cell as status arrives; no spinners, the stage word changes in place. `prefers-reduced-motion`: swaps are instant, leaders appear without drawing.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.
