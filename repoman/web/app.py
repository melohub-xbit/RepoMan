"""RepoMan evaluator workspace. Server-rendered; HTMX for partials; View Transitions for pages."""

from __future__ import annotations

import base64
import csv
import io
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_for_filename
from pygments.util import ClassNotFound

from repoman.core.types import (Batch, Claim, Decision, Finding, gate_status, Level, Precedent, Requirement, Rubric, RunStatus, Scale,
                                SourceSpan, coverage, new_id)
from repoman.store import make_store

# No fallback to the stub. It was the right scaffold while the engine was being built, but a
# silent `except ImportError` here means one bad import in the engine serves *fixture findings as
# real ones* — fabricated evidence about a real student's work, with no sign on screen. Fail loudly.
from repoman import pipeline  # noqa: E402

HERE = Path(__file__).parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    close_out_interrupted_runs()
    yield


app = FastAPI(title="RepoMan", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
tpl = Jinja2Templates(directory=HERE / "templates")
store = make_store()

STAGES = ["queued", "acquire", "probe", "verify", "claims", "contradict", "done"]
# Runs wait in this pool. Sixty submissions at once must not mean sixty agents at once: each run already
# fans out across requirements, and the model provider has one throttle for the whole account.
RUN_POOL = ThreadPoolExecutor(max_workers=int(os.environ.get("REPOMAN_PARALLEL_RUNS", "2")))
STATE_ORDER = {"CONTRADICTED": 0, "UNVERIFIED": 1, "PARTIAL": 2, "VERIFIED": 3}


def close_out_interrupted_runs() -> None:
    """Mark runs that were in progress when the process last stopped as failed.

    A run executes in a daemon thread, so stopping the server — Ctrl-C, a crash, a `--reload`
    restart — kills it mid-stage while its `status.json` still says "verify". Nothing would ever
    write to that file again, and the queue polls an unfinished row every two seconds forever.
    A dead run must read as dead.
    """
    for key in store.list("runs"):
        if not key.endswith("status.json"):
            continue
        status = store.get_json(key, {}) or {}
        if status.get("stage") in ("done", "failed"):
            continue
        stage = status.get("stage", "queued")
        store.put_json(key, RunStatus(stage="failed",
                                      detail=f"interrupted during {stage}; the server stopped mid-run"))


# --- auth: one shared token, HTTP basic. Cognito is post-hackathon. --------------

@app.middleware("http")
async def basic_auth(request: Request, call_next):
    token = os.environ.get("REPOMAN_TOKEN")
    if token and not request.url.path.startswith(("/static", "/healthz")):
        header = request.headers.get("authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(pw, token)
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="RepoMan"'})
    return await call_next(request)


# --- data ---------------------------------------------------------------------

def load_batch(batch_id: str) -> Batch:
    return Batch.model_validate(store.get_json(f"batches/{batch_id}.json"))


def load_rubric(batch: Batch) -> Rubric | None:
    return Rubric.model_validate(store.get_json(f"rubrics/{batch.rubricId}.json")) if batch.rubricId else None


def load_run(run_id: str) -> dict:
    p = f"runs/{run_id}/"
    sub = store.get_json(p + "submission.json") or {}
    batch = load_batch(sub["batchId"]) if sub else None
    ident = (sub.get("identity") or {}) if sub else {}
    if sub.get("repoUrl"):
        name = sub["repoUrl"].replace("https://github.com/", "")
    elif ident.get("team") and not (batch and batch.blind):
        name = ident["team"]
    else:
        name = f"S-{run_id[:6]}"  # blind: a stable label with no identity in it
    rubric = load_rubric(batch) if batch else None
    findings = [Finding.model_validate(f) for f in store.get_json(p + "findings.json", [])]
    decisions = [Decision.model_validate(d) for d in store.get_json(p + "decisions.json", [])]
    reqs = rubric.requirements if rubric else []
    by_req = {f.requirementId: f for f in findings}
    dec_by_req = {d.requirementId: d for d in decisions}
    flags = sorted({fl for f in findings for fl in f.flagged})
    claims = [{"req": r, "finding": by_req.get(r.id), "decision": dec_by_req.get(r.id), "pos": i + 1, "total": len(reqs)}
              for i, r in enumerate(reqs)]
    # the submission's own claims: never scored, never in coverage — shown beside the rubric
    self_claims = [Claim.model_validate(c) for c in store.get_json(p + "claims.json", [])]
    said = [{"claim": c, "finding": by_req.get(c.id), "pos": i + 1} for i, c in enumerate(self_claims)]
    verified, verifiable = coverage(findings, reqs)
    # Eligibility: None means "no gates on this rubric" or "still running" — never rendered as a failure.
    gates = gate_status(findings, reqs)
    eligible = None if not gates or None in gates.values() else all(gates.values())
    scores = [d.score for d in decisions if d.score is not None]
    tally = {}
    for f in findings:
        tally[f.state] = tally.get(f.state, 0) + 1
    return {
        "run_id": run_id, "display_name": name, "sub": sub, "batch": batch, "rubric": rubric, "claims": claims, "said": said, "flags": flags, "tally": tally,
        "gates": gates, "eligible": eligible,
        "probes": store.get_json(p + "probes.json", {}),
        # A run whose status.json has not been written yet (the thread has not started) is queued,
        # not a 500. model_validate(None) would raise here and take the whole queue page with it.
        "status": RunStatus.model_validate(store.get_json(p + "status.json", {"stage": "queued"})),
        "verified": verified, "verifiable": verifiable, "decided": len(dec_by_req),
        "total_marks": sum(scores) if scores else None, "total_weight": sum(r.weight for r in reqs),
        "manifest": store.get_json(p + "manifest.json", {}),
    }


def cohort(rows: list[dict], rubric: Rubric | None) -> dict | None:
    """Counts across the batch, per requirement and for the submissions' own claims. Counts, never a grade.

    "Six of eight never used the cache they declared" is a fact about the cohort that no single
    run can show; it is also the sentence a TA writes in the course post-mortem.
    """
    done = [r for r in rows if r["status"].stage == "done"]
    if not rubric or len(done) < 2:
        return None
    order = ("VERIFIED", "PARTIAL", "UNVERIFIED", "CONTRADICTED")
    per_req = []
    for req in rubric.requirements:
        if not req.verifiable:
            continue
        states = [c["finding"].state for r in done for c in r["claims"] if c["req"].id == req.id and c["finding"]]
        counts = {st: states.count(st) for st in order}
        worst = max((st for st in order if st != "VERIFIED"), key=lambda st: counts[st], default=None)
        per_req.append({"req": req, "counts": counts, "n": len(states),
                        "segments": [(st, counts[st] / len(states) if states else 0) for st in order],
                        "so_what": (f"{counts[worst]} of {len(states)} " + {"PARTIAL": "partial", "UNVERIFIED": "looked · not found",
                                                                            "CONTRADICTED": "contradicted"}[worst])
                        if worst and counts[worst] else f"all {len(states)} verified"})
    gate_reqs = [req for req in rubric.requirements if req.gate]
    gates = None
    if gate_reqs:
        met = sum(1 for r in done if r["eligible"] is True)
        not_met = sum(1 for r in done if r["eligible"] is False)
        gates = {"requirements": gate_reqs, "eligible": met, "not_eligible": not_met,
                 "pending": len(done) - met - not_met}

    said = [(r, c) for r in done for c in r["said"] if c["finding"]]
    held = sum(1 for _, c in said if c["finding"].state == "VERIFIED")
    failed = [{"run": r, "claim": c["claim"], "state": c["finding"].state} for r, c in said
              if c["finding"].state in ("CONTRADICTED", "UNVERIFIED")]
    failed.sort(key=lambda x: STATE_ORDER[x["state"]])
    return {"n": len(done), "per_req": per_req, "claims_total": len(said), "claims_held": held, "claims_failed": failed[:6],
            "gates": gates}


def queue_rows(batch: Batch, rubric: Rubric | None) -> list[dict]:
    rows = []
    for rid in batch.runIds:
        r = load_run(rid)
        states = [c["finding"].state for c in r["claims"] if c["finding"]]
        worst = min((STATE_ORDER[s] for s in states), default=9)
        # needs-a-human first: flagged, then contradicted, then low coverage
        r["sort"] = (0 if r["flags"] else 1, worst, r["verified"] - r["verifiable"])
        r["states"] = sorted(states, key=STATE_ORDER.get)
        rows.append(r)
    # Gates are the pre-read sort: a submission that fails one waits behind every eligible or
    # still-pending one, but is never removed — export, decisions and the page itself are unaffected.
    rows.sort(key=lambda r: (r["status"].stage == "done", r["eligible"] is False, r["sort"]))
    return rows


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    # every app page inside a batch gets the rail: the batch's submissions and where you are
    batch = ctx.get("batch")
    if batch is not None and "rail_rows" not in ctx:
        ctx["rail_rows"] = ctx.get("rows") or queue_rows(batch, ctx.get("rubric") or load_rubric(batch))
    return tpl.TemplateResponse(request, name, ctx)


# --- pages --------------------------------------------------------------------

@app.get("/healthz", include_in_schema=False)
def healthz():
    """App Runner's health check cannot sign in; this is the one page that does not ask it to."""
    return Response("ok", media_type="text/plain")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="-14 -14 28 28"><g fill="#17181b"><rect x="-1.9" y="-12" width="3.8" height="14" rx="1.9" transform="rotate(0) translate(3.4 0)"/><rect x="-1.9" y="-12" width="3.8" height="14" rx="1.9" transform="rotate(60) translate(3.4 0)"/><rect x="-1.9" y="-12" width="3.8" height="14" rx="1.9" transform="rotate(120) translate(3.4 0)"/><rect x="-1.9" y="-12" width="3.8" height="14" rx="1.9" transform="rotate(180) translate(3.4 0)"/><rect x="-1.9" y="-12" width="3.8" height="14" rx="1.9" transform="rotate(240) translate(3.4 0)"/><rect x="-1.9" y="-12" width="3.8" height="14" rx="1.9" transform="rotate(300) translate(3.4 0)"/></g></svg>')
    return Response(svg, media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return render(request, "landing.html")


@app.get("/batches", response_class=HTMLResponse)
def index(request: Request):
    batches = [Batch.model_validate(store.get_json(k)) for k in store.list("batches")]
    batches.sort(key=lambda b: b.createdAt, reverse=True)
    # Cheap per-row status for the listing: a rubric's requirement count, not a full run analysis.
    # Loading every run here would mean an index page that gets slower as a batch fills with
    # submissions; the batch page itself is where that cost belongs.
    rubrics = {b.rubricId: load_rubric(b) for b in batches if b.rubricId}
    rows = [{"batch": b, "rubric": rubrics.get(b.rubricId)} for b in batches]
    return render(request, "index.html", batches=batches, rows=rows)


@app.post("/batches")
def create_batch(name: str = Form(...), start: str = Form(""), end: str = Form(""), claims: str = Form(""),
                 blind: str = Form("")):
    b = Batch(name=name.strip() or "Untitled batch", eventWindow=(start, end) if start and end else None,
              checkClaims=claims == "on", blind=blind == "on")
    store.put_json(f"batches/{b.id}.json", b)
    return RedirectResponse(f"/batches/{b.id}/rubric", status_code=303)


@app.post("/batches/{batch_id}/delete")
def delete_batch(batch_id: str):
    """Removes the batch's own record, its rubric, its precedents, and every run's files and
    checkout. Confirmed client-side (index.html) before this is ever called — there is no undo,
    same as deleting the folder on disk would be, because that is exactly what this does."""
    key = f"batches/{batch_id}.json"
    if not store.exists(key):
        return RedirectResponse("/batches", status_code=303)
    batch = load_batch(batch_id)
    for run_id in batch.runIds:
        store.delete_prefix(f"runs/{run_id}")
    store.delete_prefix(f"imports/{batch_id}")
    if batch.rubricId:
        store.delete(f"rubrics/{batch.rubricId}.json")
    store.delete(f"precedents/{batch_id}.json")
    store.delete(key)
    return RedirectResponse("/batches", status_code=303)


@app.get("/batches/{batch_id}", response_class=HTMLResponse)
def batch_page(request: Request, batch_id: str):
    batch = load_batch(batch_id)
    rubric = load_rubric(batch)
    precedents = store.get_json(f"precedents/{batch_id}.json", [])
    rows = queue_rows(batch, rubric)
    # Where a human is needed first: anything flagged, plus anything contradicted.
    need = sum(1 for r in rows if r["flags"] or "CONTRADICTED" in r["states"])
    # Cross-submission similarity is a fact about the batch, not about any one run, so it is
    # computed here rather than baked into findings that were already shown to a human.
    names = {r["run_id"]: (r["sub"].get("repoUrl") or r["run_id"]).replace("https://github.com/", "")
             for r in rows}
    overlaps = [{**o, "text": f"{names.get(o['a'], o['a'])} and {names.get(o['b'], o['b'])} share "
                              f"{o['sharedCount']} identical file{'' if o['sharedCount'] == 1 else 's'} "
                              f"({round(o['share'] * 100)}% of the smaller submission)",
                 "a_name": names.get(o["a"], o["a"]), "b_name": names.get(o["b"], o["b"])}
                for o in pipeline.batch_similarity(store, batch.runIds)]
    return render(request, "batch.html", batch=batch, rubric=rubric, rows=rows, need=need, cohort=cohort(rows, rubric),
                  precedents=precedents, overlaps=overlaps)


@app.get("/batches/{batch_id}/rubric", response_class=HTMLResponse)
def rubric_page(request: Request, batch_id: str, error: str = ""):
    batch = load_batch(batch_id)
    return render(request, "rubric.html", batch=batch, rubric=load_rubric(batch), error=error[:300])


def save_rubric(batch: Batch, rubric: Rubric) -> None:
    store.put_json(f"rubrics/{rubric.id}.json", rubric)
    if batch.rubricId != rubric.id:
        batch.rubricId = rubric.id
        store.put_json(f"batches/{batch.id}.json", batch)


@app.post("/batches/{batch_id}/rubric/compile")
def rubric_compile(batch_id: str, source: str = Form(...)):
    """Compile pasted prose and append the result to whatever the editor already holds."""
    batch = load_batch(batch_id)
    try:
        compiled = pipeline.compile_rubric(source, ["repo", "readme", "report", "deploy"])
    except Exception as e:  # the model is the only thing that can fail here; the editor still works without it
        msg = f"The model could not compile the rubric ({type(e).__name__}: {str(e)[:160]}). Add requirements by hand below, or try again."
        return RedirectResponse(f"/batches/{batch_id}/rubric?error={quote(msg)}", status_code=303)
    old = load_rubric(batch)
    if old is None:
        save_rubric(batch, compiled)
    else:
        offset = len(old.sourceText) + 1
        rubric = Rubric(id=old.id, version=old.version + 1, sourceText=old.sourceText + "\n" + source,
                        compiledBy=compiled.compiledBy, requirements=list(old.requirements))
        for r in compiled.requirements:
            if r.sourceSpan:
                r.sourceSpan = SourceSpan(startChar=r.sourceSpan.startChar + offset, endChar=r.sourceSpan.endChar + offset)
            rubric.requirements.append(r.model_copy(update={"rubricId": rubric.id}))
        save_rubric(batch, rubric)
    return RedirectResponse(f"/batches/{batch_id}/rubric", status_code=303)


def scale_from_form(form, rid: str) -> Scale:
    kind = form.get(f"scale-{rid}", "points")
    if kind != "levels":
        return Scale(kind=kind)
    levels = [Level(points=float(p or 0), label=l.strip(), description=d.strip())
              for p, l, d in zip(form.getlist(f"lp-{rid}"), form.getlist(f"ll-{rid}"), form.getlist(f"ld-{rid}")) if l.strip()]
    return Scale(kind="levels", levels=levels)


@app.post("/batches/{batch_id}/rubric")
async def rubric_approve(request: Request, batch_id: str):
    form = await request.form()
    batch = load_batch(batch_id)
    old = load_rubric(batch) or Rubric(sourceText="", compiledBy="evaluator")  # every line typed in by hand
    rubric = Rubric(id=old.id, version=old.version + 1, sourceText=old.sourceText, compiledBy=old.compiledBy)
    for rid in form.getlist("id"):
        if form.get(f"delete-{rid}"):
            continue
        prev = next((r for r in old.requirements if r.id == rid), None)
        scale = scale_from_form(form, rid)
        weight = max(l.points for l in scale.levels) if scale.kind == "levels" else float(form.get(f"weight-{rid}") or 0)
        verifiable = form.get(f"verifiable-{rid}") == "on"
        # Clamped, not trusted: the form disables the checkbox for a non-"check" scale, but a raw
        # POST could still set it, and a gate must stay a real check (docs/03) or the row 500s.
        gate = form.get(f"gate-{rid}") == "on" and verifiable and scale.kind == "check"
        rubric.requirements.append(Requirement(
            id=rid, rubricId=rubric.id, title=form.get(f"title-{rid}", "").strip(),
            statement=form.get(f"statement-{rid}", "").strip(), weight=weight, scale=scale,
            sourceSpan=prev.sourceSpan if prev else None,
            verifiable=verifiable, gate=gate,
            unverifiableReason=prev.unverifiableReason if prev else None,
            proposedBy="evaluator",  # once the human edits and approves, it is theirs
        ))
    save_rubric(batch, rubric)
    return RedirectResponse(f"/batches/{batch_id}", status_code=303)


@app.post("/batches/{batch_id}/submissions")
async def add_submission(batch_id: str, repo_url: str = Form(""), deploy_url: str = Form(""),
                         report: UploadFile | None = None, zipfile: UploadFile | None = None, urls: str = Form(""),
                         bulk: UploadFile | None = None, skeleton: UploadFile | None = None):
    """Four ways in, one queue out: a URL, a zip, a list of URLs, or one archive holding many submissions."""
    batch = load_batch(batch_id)
    rubric = load_rubric(batch)
    if rubric is None or not rubric.requirements:
        # Nothing to investigate against. The page already hides the form until a rubric exists;
        # this catches the direct POST rather than starting runs that can only fail.
        return RedirectResponse(f"/batches/{batch_id}/rubric", status_code=303)
    if skeleton and skeleton.filename:
        batch.baseline = (await skeleton.read()).decode("utf-8", errors="replace")
        store.put_json(f"batches/{batch.id}.json", batch)

    jobs: list[dict] = []  # each becomes one run
    if bulk and bulk.filename:
        jobs += await import_archive(batch, bulk)
    entries = [u.strip() for u in urls.splitlines() if u.strip()] if urls.strip() else [repo_url.strip()]
    entries = [e for e in entries if e] or ([""] if (zipfile and zipfile.filename) else [])
    for entry in entries:
        job = {"repo_url": entry or None, "zip_path": None, "report_path": None, "label": None}
        run_id = job["run_id"] = new_id()
        prefix = f"runs/{run_id}/"
        if report and report.filename:
            job["report_path"] = str(store.local_dir(prefix) / "report.pdf")
            Path(job["report_path"]).write_bytes(await report.read())
        if zipfile and zipfile.filename:
            job["zip_path"] = str(store.local_dir(prefix) / "submission.zip")
            Path(job["zip_path"]).write_bytes(await zipfile.read())
        jobs.append(job)

    precedents = [Precedent.model_validate(p) for p in store.get_json(f"precedents/{batch_id}.json", [])]
    for job in jobs:
        run_id, prefix = job["run_id"], f"runs/{job['run_id']}/"
        store.put_json(prefix + "submission.json", {
            "id": run_id, "batchId": batch_id, "commitSha": "", "artifacts": [], "repoUrl": job.get("repo_url"),
            "source": "github" if job.get("repo_url") else ("dir" if job.get("dir_path") else "zip"),
            "identity": {"team": job["label"]} if job.get("label") and not batch.blind else None})
        store.put_json(prefix + "status.json", RunStatus(stage="queued"))
        batch.runIds.append(run_id)
    store.put_json(f"batches/{batch.id}.json", batch)
    for job in jobs:
        RUN_POOL.submit(_run, job["run_id"], batch, rubric, job.get("repo_url"), job.get("zip_path"), job.get("report_path"),
                        deploy_url.strip() or None, precedents, job.get("dir_path"), job.get("label"))
    return RedirectResponse(f"/batches/{batch_id}", status_code=303)


async def import_archive(batch: Batch, upload: UploadFile) -> list[dict]:
    """One archive, many submissions: the shape DOMjudge, Moodle and GitHub Classroom export.

    Every top-level folder is one submission and its name is the label (a student id, a team). Loose
    files at the top level are one submission each. If the batch has no skeleton and there are three
    or more C++ sources, the skeleton is derived from what they all share (`probes.cpp.derive_baseline`).
    """
    from repoman.probes import cpp

    # Deliberately NOT under "batches/<id>/..." — store.list("batches") globs that prefix
    # recursively for every batch's JSON, and a bulk import's unpacked files would be
    # picked up and fail Batch.model_validate. Keep the two namespaces disjoint.
    holding = store.local_dir(f"imports/{batch.id}/{new_id()}")
    archive = holding.with_suffix(".zip")
    archive.write_bytes(await upload.read())
    entries = pipeline.expand_bulk_import(archive, holding)
    archive.unlink(missing_ok=True)
    jobs = [{"run_id": new_id(), "dir_path": str(d), "label": label} for d, label in entries]
    if not batch.baseline:
        sources = [f.read_text(encoding="utf-8", errors="replace")
                   for j in jobs for f in Path(j["dir_path"]).rglob("*") if f.suffix in cpp.CPP_SUFFIXES]
        derived = cpp.derive_baseline(sources)
        if derived:
            batch.baseline = derived
    return jobs


def _run(run_id, batch, rubric, repo_url, zip_path, report_path, deploy_url, precedents, dir_path=None, label=None):
    try:
        pipeline.run_submission(store, batch.id, rubric, run_id=run_id, repo_url=repo_url, zip_path=zip_path,
                                dir_path=dir_path, report_path=report_path, deploy_url=deploy_url,
                                event_window=batch.eventWindow, precedents=precedents, check_claims=batch.checkClaims,
                                baseline=batch.baseline, label=None if batch.blind else label)
    except Exception as e:  # a failed run is a visible row, never a missing one
        store.put_json(f"runs/{run_id}/status.json", RunStatus(stage="failed", detail=f"{type(e).__name__}: {e}"[:200]))


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: str):
    r = load_run(run_id)
    # Shared code is a fact about a pair, so from inside one submission it reads as "identical to
    # that one" — with a link, because the only useful next move is to open both and compare.
    overlaps = []
    if r["batch"]:
        for o in pipeline.batch_similarity(store, r["batch"].runIds):
            if run_id in (o["a"], o["b"]):
                other = o["b"] if o["a"] == run_id else o["a"]
                sub = store.get_json(f"runs/{other}/submission.json", {}) or {}
                overlaps.append({"other": other, "shared": o["shared"], "shared_count": o["sharedCount"],
                                 "other_name": (sub.get("repoUrl") or other).replace("https://github.com/", "")})
    return render(request, "run.html", **r, overlaps=overlaps)


# --- partials (HTMX) ----------------------------------------------------------

@app.get("/runs/{run_id}/status", response_class=HTMLResponse)
def run_status(request: Request, run_id: str):
    r = load_run(run_id)
    resp = tpl.TemplateResponse(request, "_row.html", {"r": r})
    if r["status"].stage in ("done", "failed"):
        resp.headers["HX-Trigger"] = "run-finished"
    return resp


@app.get("/runs/{run_id}/evidence/{ev_id}", response_class=HTMLResponse)
def evidence(request: Request, run_id: str, ev_id: str):
    r = load_run(run_id)
    ev = next((e for c in r["claims"] if c["finding"] for e in c["finding"].evidence if e.id == ev_id), None)
    if ev is None:  # a claim's own words, or the evidence of the finding about it
        ev = next((e for c in r["said"] for e in [c["claim"].source] + (c["finding"].evidence if c["finding"] else [])
                   if e.id == ev_id), None)
    if ev is None:
        return HTMLResponse("", status_code=404)
    loc = ev.locator
    body = None
    if loc.kind == "file_range":
        path = store.local_dir(f"runs/{run_id}/repo") / loc.path
        if path.exists():
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            lo, hi = max(1, loc.startLine - 6), min(len(lines), loc.endLine + 6)
            chunk = "\n".join(lines[lo - 1:hi])
            try:
                lexer = get_lexer_for_filename(loc.path)
            except ClassNotFound:
                lexer = TextLexer()
            fmt = HtmlFormatter(nowrap=True)
            html_lines = highlight(chunk, lexer, fmt).rstrip("\n").split("\n")
            body = [{"n": lo + i, "html": h, "cited": loc.startLine <= lo + i <= loc.endLine} for i, h in enumerate(html_lines)]
    elif loc.kind == "doc_span":
        pages = store.get_json(f"runs/{run_id}/report_pages.json", {})
        text = pages.get(str(loc.page))
        if text is not None:  # escape first: submission text is hostile input
            body = Markup(str(escape(text)).replace(str(escape(ev.quote)), f"<mark>{escape(ev.quote)}</mark>"))
    return tpl.TemplateResponse(request, "_evidence.html", {"ev": ev, "body": body, "sub": r["sub"]})


@app.post("/runs/{run_id}/decisions", response_class=HTMLResponse)
def decide(request: Request, run_id: str, requirement_id: str = Form(...), action: str = Form(...),
           score: str = Form(""), met: str = Form(""), level: str = Form(""), note: str = Form(""), precedent: str = Form("")):
    r = load_run(run_id)
    claim = next(c for c in r["claims"] if c["req"].id == requirement_id)
    req, label = claim["req"], None
    if req.scale.kind == "check":  # the form carries the human's choice in the scale's own terms; marks follow from it
        marks = (req.weight if met == "1" else 0.0) if met else None
    elif req.scale.kind == "levels":
        chosen = req.scale.levels[int(level)] if level.strip() else None
        marks, label = (chosen.points, chosen.label) if chosen else (None, None)
    else:
        marks = float(score) if score.strip() else None
    d = Decision(submissionId=r["sub"]["id"], requirementId=requirement_id, evaluatorId="evaluator",
                 score=marks, level=label, note=note.strip(),
                 overrodeFindingId=claim["finding"].id if action == "override" and claim["finding"] else None)
    decisions = [x for x in store.get_json(f"runs/{run_id}/decisions.json", []) if x["requirementId"] != requirement_id]
    decisions.append(d.model_dump())
    store.put_json(f"runs/{run_id}/decisions.json", decisions)  # ponytail: read-modify-write, one evaluator per batch
    if action == "override" and precedent.strip():
        key = f"precedents/{r['batch'].id}.json"
        ps = store.get_json(key, [])
        ps.append(Precedent(batchId=r["batch"].id, requirementId=requirement_id, rule=precedent.strip(),
                            derivedFromDecisionId=d.id).model_dump())
        store.put_json(key, ps)
    r = load_run(run_id)
    claim = next(c for c in r["claims"] if c["req"].id == requirement_id)
    resp = tpl.TemplateResponse(request, "_claim.html", {"c": claim, **r})
    resp.headers["HX-Trigger-After-Swap"] = "decided"
    return resp


@app.get("/runs/{run_id}/live", response_class=HTMLResponse)
def run_live(request: Request, run_id: str):
    """While a run is in progress the page polls this; finished cards are swapped in out-of-band."""
    r = load_run(run_id)
    if r["status"].stage in ("done", "failed"):
        return HTMLResponse('<div id="live"></div>', headers={"HX-Refresh": "true"})
    return tpl.TemplateResponse(request, "_live.html", {**r, "oob": True})


@app.get("/runs/{run_id}/facts", response_class=HTMLResponse)
def facts(request: Request, run_id: str):
    r = load_run(run_id)
    return tpl.TemplateResponse(request, "_facts.html", r)


# --- exports ------------------------------------------------------------------

@app.get("/batches/{batch_id}/export.csv")
def export_csv(batch_id: str):
    batch = load_batch(batch_id)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["submission", "eligible", "requirement", "gate", "weight", "state", "confidence", "evaluator_score", "evaluator_level", "note", "verified", "verifiable"])
    for r in queue_rows(batch, load_rubric(batch)):
        elig = {None: "", True: "yes", False: "no"}[r["eligible"]]
        for c in r["claims"]:
            f, d = c["finding"], c["decision"]
            w.writerow([r["sub"].get("repoUrl") or r["run_id"], elig, c["req"].title, "yes" if c["req"].gate else "",
                        c["req"].weight, f.state if f else "not checked", f.confidence if f else "", d.score if d else "",
                        d.level if d else "", d.note if d else "", r["verified"], r["verifiable"]])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{batch.name}.csv"'})


@app.get("/runs/{run_id}/feedback.md")
def feedback(run_id: str):
    """The student-facing note, built from what the evaluator *accepted* — not from RepoMan's states.

    Nothing here is sent anywhere: docs/07 Q2 is open on whether RepoMan ever delivers this, so it
    is a download the evaluator edits and forwards themselves. No states jargon, no confidence, no
    flags, and no finding the evaluator has not signed off on.
    """
    r = load_run(run_id)
    decided = [c for c in r["claims"] if c["decision"]]
    lines = [f"# Feedback — {r['sub'].get('repoUrl') or run_id}", ""]
    if not decided:
        lines.append("_No decisions have been recorded yet, so there is nothing to send._")
        return Response("\n".join(lines), media_type="text/markdown")

    for c in decided:
        req, f, d = c["req"], c["finding"], c["decision"]
        head = f"## {req.title}"
        if d.score is not None:
            head += f" — {d.score:g} of {req.weight:g}" + (f" ({d.level})" if d.level else "")
        lines += [head, ""]
        if d.note:
            lines += [d.note, ""]
        # Only findings the evaluator accepted, described plainly, with the places to look.
        if f and not d.overrodeFindingId:
            lines += [f.summary, ""]
            for e in f.evidence[:4]:
                lines.append(f"- {permalink(e.locator, r['sub'])}")
            lines.append("")
    total = sum(c["decision"].score for c in decided if c["decision"].score is not None)
    lines += ["---", f"Total recorded so far: {total:g} of {r['total_weight']:g}."]
    return Response("\n".join(lines), media_type="text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="feedback-{run_id}.md"'})


@app.get("/runs/{run_id}/packet.md")
def packet(run_id: str):
    r = load_run(run_id)
    elig_line = {None: "", True: "**Meets every eligibility gate.**", False: "**Fails an eligibility gate.**"}[r["eligible"]]
    lines = [f"# Evidence packet — {r['sub'].get('repoUrl') or run_id}", f"Commit `{r['sub'].get('commitSha', '')}` · "
             f"{r['verified']} of {r['verifiable']} requirements have verified evidence"
             + (f" · {elig_line}" if elig_line else ""), ""]
    for c in r["claims"]:
        req, f, d = c["req"], c["finding"], c["decision"]
        lines += [f"## {'[GATE] ' if req.gate else ''}{req.title} — {req.weight} marks", f"_{req.statement}_", ""]
        if not req.verifiable:
            lines += [f"Not checked by RepoMan: {req.unverifiableReason}", ""]
        elif f:
            lines += [f"**{f.state}** · confidence {f.confidence} — {f.confidenceReason}", "", f.summary, ""]
            for e in f.evidence:
                lines.append(f"- {permalink(e.locator, r['sub'])} ({e.provenance}) — `{e.quote.splitlines()[0][:120]}`")
            lines.append("")
        if d:
            lines += [f"**Evaluator:** {'override' if d.overrodeFindingId else 'accepted'} · "
                      f"{d.level + ' · ' if d.level else ''}marks {d.score if d.score is not None else '—'} · {d.note}", ""]
    if r["said"]:
        lines += ["# What the submission said about itself", "_Checked the same way; not scored._", ""]
        for c in r["said"]:
            claim, f = c["claim"], c["finding"]
            lines += [f"## {claim.statement}", f"Said at {permalink(claim.source.locator, r['sub'])}: “{claim.source.quote[:200]}”", ""]
            if f:
                lines += [f"**{f.state}** — {f.summary}", ""]
                lines += [f"- {permalink(e.locator, r['sub'])} ({e.provenance}) — `{e.quote.splitlines()[0][:120]}`" for e in f.evidence] + [""]
    return Response("\n".join(lines), media_type="text/markdown")


# --- template helpers -----------------------------------------------------------

def permalink(loc, sub: dict) -> str:
    if loc.kind == "file_range":
        base = sub.get("repoUrl")
        text = f"{loc.path}:{loc.startLine}" + (f"–{loc.endLine}" if loc.endLine != loc.startLine else "")
        return f"{base}/blob/{loc.commitSha}/{loc.path}#L{loc.startLine}-L{loc.endLine}" if base else text
    if loc.kind == "doc_span":
        return f"report p.{loc.page}"
    if loc.kind == "http_capture":
        return loc.url
    return f"{sub.get('repoUrl', '')}/commit/{loc.commitSha}"


def locator_text(loc) -> str:
    if loc.kind == "file_range":
        short = "/".join(loc.path.split("/")[-2:])
        return f"{short}:{loc.startLine}" + (f"–{loc.endLine}" if loc.endLine != loc.startLine else "")
    if loc.kind == "doc_span":
        return f"report · p.{loc.page}"
    if loc.kind == "http_capture":
        return f"{loc.url.replace('https://', '')} · {loc.status}"
    return f"commit {loc.commitSha[:7]}"


def mark(state: str) -> Markup:
    return Markup(f'<svg class="mark" aria-hidden="true"><use href="#m-{escape(state)}"/></svg>')


def statechip(state: str, word: str | None = None) -> Markup:
    if word is None:
        word = "looked · not found" if state == "UNVERIFIED" else state.lower()
    return Markup(f'<span class="state state-{escape(state)}" title="{escape(state.lower())}">{mark(state)}{escape(word)}</span>')


tpl.env.globals.update(permalink=permalink, locator_text=locator_text, STAGES=STAGES, mark=mark, statechip=statechip,
                       asset_v=lambda: int((HERE / 'static' / 'app.css').stat().st_mtime))
tpl.env.filters["short"] = lambda s, n=90: (s if len(s) <= n else s[: n - 1] + "…")
