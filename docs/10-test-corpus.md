# 10 — Test corpus: a real hackathon, its real rubric, its real winners

The fixtures prove the mechanics. They cannot prove the product, because we wrote both the
rubric and the repositories. The corpus below is a finished, public hackathon whose judging
criteria and winning submissions are on Devpost, so every RepoMan finding can be checked by a
human against the same evidence a judge had.

## The event

**HackHarvard 2025 — "Compile the Decade"**, Oct 3–5 2025, 160 submissions, 18 winners.
Devpost: https://hackharvard-2025.devpost.com/ (criteria at the bottom of the overview page;
the `/rules` page is only a code-of-conduct link).

The rubric is in `fixtures/corpus/hackharvard-2025-rubric.md`, verbatim. It is deliberately
hard for us: four holistic lines and no weights. What the compiler *should* do with it —

| Line | Expected compile |
|---|---|
| Innovation, Creativity and Impact | `verifiable: false` — nothing in a repo shows novelty |
| Technical Complexity | decomposed into `proposedBy: "repoman"` lines the evaluator edits (e.g. "uses an ML model at runtime", "has a backend API", "persists data") |
| Functionality and Usability | partly: "runs without error" is the deploy probe + (later) the sandbox run; "intuitive user journey" stays with the human |
| Collaboration and Learning | the git probe's contribution share and timeline; the *learning* half stays with the human |

If the compiler marks all four verifiable, or all four unverifiable, that is a prompt bug.

## The submissions

`fixtures/corpus/hackharvard-2025.csv` — ten winners with public repos, one URL per line, ready
for the batch page's CSV import. Each row's Devpost page makes claims the claims ledger can
test; the interesting ones:

| Project | Repo | Claims worth checking |
|---|---|---|
| Post Surgery Pillow (1st) | quynhanh726/PSP-Post-Surgery-Pillow- | Description says Node.js + Express + **PostgreSQL**; "Built With" lists **MongoDB**. Team bios claim post-quantum encryption and GNN inference. |
| ResQme (2nd) | lordoftheseas/ResQme | ESP32 mesh + GPS + web console; Expo app, Next.js, Supabase — a lot of surface for a weekend |
| Yumi (3rd) | scrappydevs/Yummy | Gemini function-calling tools by name; "image resize cut upload time 70%"; deployed on Render/Netlify |
| SiteOps | rajvratzoom/siteops-final | Solo: YOLOv8, MiDaS, ByteTrack, 20 FPS, RAG agent, WebSocket streaming — every one is greppable |
| Visa Verify | HaoChiBao/hackharvard2025 | Behavioural biometrics + VPN detection; "no ML" (honest) |
| Veritas | achneerov/HackHarvard | "HTTPS encryption + salted hashing"; MFA between customer and processor |
| Halo | Harpith2/Halo | ARKit joints by name (spine7Joint…), ElevenLabs, Cloudflare Workers AI; Built With also lists redis, chromadb, sqlite |
| Doodle World | mongj/doodle-world | Gaussian splats + glb, WebSocket sync, Cloud Run; "40% load-time cut" |
| HaloAudit | Evandabest/Hackharvard2025 | Electron + Swift + Next.js + LangGraph; admits Cloudflare wiring trouble |
| Lingua Spatial | SaketR3/Vision-Pro-Language-Immersion | visionOS + CreateML; second repo holds the Flask API |

The "Built With" tag list versus the description is exactly the claim-versus-code gap the
product exists for, and here it was written by the teams themselves.

## How to run it

1. New batch, event window `2025-10-03` → `2025-10-05` (so the git probe can say what predates
   the event), "check what each submission says about itself" on.
2. Paste the rubric; **read the compiled table before approving** — this is the compiler test.
3. Paste the CSV into the batch page's bulk field.
4. Sanity-check three findings by hand against the repo before believing any of it.

## Cost

On Bedrock Sonnet, budget ~$1.50 per submission uncached (docs/04), so the ten cost ≈ $15 and
each rehearsal of the full batch another ≈ $15. Run **three first** (PSP, SiteOps, Yumi — the
richest claims), read them, then the rest. Keep the manifest's `usage` and replace the estimate.

## What this corpus is not

These are real students' public repositories. RepoMan produces evidence for a human, never a
grade, and nothing about a named team leaves the machine it was run on. Use blind mode
(`identity = null`) for any screenshot or demo, and do not publish findings about these
projects.

Sources: [HackHarvard 2025 on Devpost](https://hackharvard-2025.devpost.com/),
[project gallery](https://hackharvard-2025.devpost.com/project-gallery),
[Devpost on judging criteria](https://info.devpost.com/blog/understanding-hackathon-submission-and-judging-criteria).
