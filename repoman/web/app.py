"""RepoMan evaluator workspace. Server-rendered; HTMX for partials; View Transitions for pages."""

from __future__ import annotations

import base64
import csv
import io
import os
import secrets
import threading
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

from repoman.core.types import (Batch, Decision, Finding, Precedent, Requirement, Rubric, RunStatus, SourceSpan,
                                coverage, new_id)
from repoman.store import make_store

try:
    from repoman import pipeline  # type: ignore
except ImportError:  # engine not landed yet
    from repoman import pipeline_stub as pipeline  # type: ignore

HERE = Path(__file__).parent
app = FastAPI(title="RepoMan")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
tpl = Jinja2Templates(directory=HERE / "templates")
store = make_store()

STAGES = ["queued", "acquire", "probe", "verify", "contradict", "done"]
STATE_ORDER = {"CONTRADICTED": 0, "UNVERIFIED": 1, "PARTIAL": 2, "VERIFIED": 3}


# --- auth: one shared token, HTTP basic. Cognito is post-hackathon. --------------

@app.middleware("http")
async def basic_auth(request: Request, call_next):
    token = os.environ.get("REPOMAN_TOKEN")
    if token and not request.url.path.startswith("/static"):
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
    rubric = load_rubric(batch) if batch else None
    findings = [Finding.model_validate(f) for f in store.get_json(p + "findings.json", [])]
    decisions = [Decision.model_validate(d) for d in store.get_json(p + "decisions.json", [])]
    reqs = rubric.requirements if rubric else []
    by_req = {f.requirementId: f for f in findings}
    dec_by_req = {d.requirementId: d for d in decisions}
    flags = sorted({fl for f in findings for fl in f.flagged})
    claims = [{"req": r, "finding": by_req.get(r.id), "decision": dec_by_req.get(r.id)} for r in reqs]
    verified, verifiable = coverage(findings, reqs)
    scores = [d.score for d in decisions if d.score is not None]
    return {
        "run_id": run_id, "sub": sub, "batch": batch, "rubric": rubric, "claims": claims, "flags": flags,
        "probes": store.get_json(p + "probes.json", {}), "status": RunStatus.model_validate(store.get_json(p + "status.json")),
        "verified": verified, "verifiable": verifiable, "decided": len(dec_by_req),
        "total_marks": sum(scores) if scores else None, "total_weight": sum(r.weight for r in reqs),
        "manifest": store.get_json(p + "manifest.json", {}),
    }


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
    rows.sort(key=lambda r: (r["status"].stage == "done", r["sort"]))
    return rows


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    return tpl.TemplateResponse(request, name, ctx)


# --- pages --------------------------------------------------------------------

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke-linecap="round" stroke-linejoin="round">'
           '<path d="M3 6.5h9M3 12h9M3 17.5h6" stroke="#15171b" stroke-width="2"/><path d="M15.5 3.5v17" stroke="#6b7078" stroke-width="1.2"/>'
           '<path d="M17.5 12.5l2 2.2 3.5-4.7" stroke="#1f5fbf" stroke-width="2.2"/></svg>')
    return Response(svg, media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return render(request, "landing.html")


@app.get("/batches", response_class=HTMLResponse)
def index(request: Request):
    batches = [Batch.model_validate(store.get_json(k)) for k in store.list("batches")]
    batches.sort(key=lambda b: b.createdAt, reverse=True)
    return render(request, "index.html", batches=batches)


@app.post("/batches")
def create_batch(name: str = Form(...), start: str = Form(""), end: str = Form("")):
    b = Batch(name=name.strip() or "Untitled batch", eventWindow=(start, end) if start and end else None)
    store.put_json(f"batches/{b.id}.json", b)
    return RedirectResponse(f"/batches/{b.id}/rubric", status_code=303)


@app.get("/batches/{batch_id}", response_class=HTMLResponse)
def batch_page(request: Request, batch_id: str):
    batch = load_batch(batch_id)
    rubric = load_rubric(batch)
    precedents = store.get_json(f"precedents/{batch_id}.json", [])
    return render(request, "batch.html", batch=batch, rubric=rubric, rows=queue_rows(batch, rubric), precedents=precedents)


@app.get("/batches/{batch_id}/rubric", response_class=HTMLResponse)
def rubric_page(request: Request, batch_id: str):
    batch = load_batch(batch_id)
    return render(request, "rubric.html", batch=batch, rubric=load_rubric(batch))


@app.post("/batches/{batch_id}/rubric/compile")
def rubric_compile(batch_id: str, source: str = Form(...)):
    batch = load_batch(batch_id)
    rubric = pipeline.compile_rubric(source, ["repo", "readme", "report", "deploy"])
    store.put_json(f"rubrics/{rubric.id}.json", rubric)
    batch.rubricId = rubric.id
    store.put_json(f"batches/{batch.id}.json", batch)
    return RedirectResponse(f"/batches/{batch_id}/rubric", status_code=303)


@app.post("/batches/{batch_id}/rubric")
async def rubric_approve(request: Request, batch_id: str):
    form = await request.form()
    batch = load_batch(batch_id)
    old = load_rubric(batch)
    rubric = Rubric(id=old.id, version=old.version + 1, sourceText=old.sourceText, compiledBy=old.compiledBy)
    ids = form.getlist("id")
    for i, rid in enumerate(ids):
        if form.get(f"delete-{rid}"):
            continue
        prev = next((r for r in old.requirements if r.id == rid), None)
        rubric.requirements.append(Requirement(
            id=rid, rubricId=rubric.id, title=form.get(f"title-{rid}", "").strip(),
            statement=form.get(f"statement-{rid}", "").strip(), weight=float(form.get(f"weight-{rid}") or 0),
            sourceSpan=prev.sourceSpan if prev else SourceSpan(startChar=0, endChar=0),
            verifiable=form.get(f"verifiable-{rid}") == "on",
            unverifiableReason=prev.unverifiableReason if prev else None,
            proposedBy="evaluator",  # once the human edits and approves, it is theirs
        ))
    store.put_json(f"rubrics/{rubric.id}.json", rubric)
    return RedirectResponse(f"/batches/{batch_id}", status_code=303)


@app.post("/batches/{batch_id}/submissions")
async def add_submission(batch_id: str, repo_url: str = Form(""), deploy_url: str = Form(""),
                         report: UploadFile | None = None, zipfile: UploadFile | None = None, urls: str = Form("")):
    batch = load_batch(batch_id)
    rubric = load_rubric(batch)
    entries = [u.strip() for u in urls.splitlines() if u.strip()] if urls.strip() else [repo_url.strip()]
    for entry in entries:
        run_id = new_id()
        prefix = f"runs/{run_id}/"
        report_path = zip_path = None
        if report and report.filename:
            report_path = str(store.local_dir(prefix) / "report.pdf")
            Path(report_path).write_bytes(await report.read())
        if zipfile and zipfile.filename:
            zip_path = str(store.local_dir(prefix) / "submission.zip")
            Path(zip_path).write_bytes(await zipfile.read())
        store.put_json(prefix + "submission.json", {"id": run_id, "batchId": batch_id, "source": "zip" if zip_path else "github",
                                                    "repoUrl": entry or None, "commitSha": "", "artifacts": []})
        store.put_json(prefix + "status.json", RunStatus(stage="queued"))
        batch.runIds.append(run_id)
        store.put_json(f"batches/{batch.id}.json", batch)
        precedents = [Precedent.model_validate(p) for p in store.get_json(f"precedents/{batch_id}.json", [])]
        threading.Thread(target=_run, args=(run_id, batch, rubric, entry or None, zip_path, report_path,
                                            deploy_url.strip() or None, precedents), daemon=True).start()
    return RedirectResponse(f"/batches/{batch_id}", status_code=303)


def _run(run_id, batch, rubric, repo_url, zip_path, report_path, deploy_url, precedents):
    try:
        pipeline.run_submission(store, batch.id, rubric, run_id=run_id, repo_url=repo_url, zip_path=zip_path,
                                report_path=report_path, deploy_url=deploy_url, event_window=batch.eventWindow,
                                precedents=precedents)
    except Exception as e:  # a failed run is a visible row, never a missing one
        store.put_json(f"runs/{run_id}/status.json", RunStatus(stage="failed", detail=f"{type(e).__name__}: {e}"[:200]))


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: str):
    return render(request, "run.html", **load_run(run_id))


# --- partials (HTMX) ----------------------------------------------------------

@app.get("/runs/{run_id}/status", response_class=HTMLResponse)
def run_status(request: Request, run_id: str):
    r = load_run(run_id)
    resp = render(request, "_row.html", r=r)
    if r["status"].stage in ("done", "failed"):
        resp.headers["HX-Trigger"] = "run-finished"
    return resp


@app.get("/runs/{run_id}/evidence/{ev_id}", response_class=HTMLResponse)
def evidence(request: Request, run_id: str, ev_id: str):
    r = load_run(run_id)
    ev = next((e for c in r["claims"] if c["finding"] for e in c["finding"].evidence if e.id == ev_id), None)
    if ev is None:
        return HTMLResponse("", status_code=404)
    loc = ev.locator
    body = None
    if loc.kind == "file_range":
        path = store.local_dir(f"runs/{run_id}/repo") / loc.path
        if path.exists():
            lines = path.read_text(errors="replace").splitlines()
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
    return render(request, "_evidence.html", ev=ev, body=body, sub=r["sub"])


@app.post("/runs/{run_id}/decisions", response_class=HTMLResponse)
def decide(request: Request, run_id: str, requirement_id: str = Form(...), action: str = Form(...),
           score: str = Form(""), note: str = Form(""), precedent: str = Form("")):
    r = load_run(run_id)
    claim = next(c for c in r["claims"] if c["req"].id == requirement_id)
    d = Decision(submissionId=r["sub"]["id"], requirementId=requirement_id, evaluatorId="evaluator",
                 score=float(score) if score.strip() else None, note=note.strip(),
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
    resp = render(request, "_claim.html", c=claim, **r)
    resp.headers["HX-Trigger-After-Swap"] = "decided"
    return resp


@app.get("/runs/{run_id}/masthead", response_class=HTMLResponse)
def masthead(request: Request, run_id: str):
    return render(request, "_masthead.html", **load_run(run_id))


# --- exports ------------------------------------------------------------------

@app.get("/batches/{batch_id}/export.csv")
def export_csv(batch_id: str):
    batch = load_batch(batch_id)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["submission", "requirement", "weight", "state", "confidence", "evaluator_score", "note", "verified", "verifiable"])
    for r in queue_rows(batch, load_rubric(batch)):
        for c in r["claims"]:
            f, d = c["finding"], c["decision"]
            w.writerow([r["sub"].get("repoUrl") or r["run_id"], c["req"].title, c["req"].weight,
                        f.state if f else "not checked", f.confidence if f else "", d.score if d else "",
                        d.note if d else "", r["verified"], r["verifiable"]])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{batch.name}.csv"'})


@app.get("/runs/{run_id}/packet.md")
def packet(run_id: str):
    r = load_run(run_id)
    lines = [f"# Evidence packet — {r['sub'].get('repoUrl') or run_id}", f"Commit `{r['sub'].get('commitSha', '')}` · "
             f"{r['verified']} of {r['verifiable']} requirements have verified evidence", ""]
    for c in r["claims"]:
        req, f, d = c["req"], c["finding"], c["decision"]
        lines += [f"## {req.title} — {req.weight} marks", f"_{req.statement}_", ""]
        if not req.verifiable:
            lines += [f"Not checked by RepoMan: {req.unverifiableReason}", ""]
        elif f:
            lines += [f"**{f.state}** · confidence {f.confidence} — {f.confidenceReason}", "", f.summary, ""]
            for e in f.evidence:
                lines.append(f"- {permalink(e.locator, r['sub'])} ({e.provenance}) — `{e.quote.splitlines()[0][:120]}`")
            lines.append("")
        if d:
            lines += [f"**Evaluator:** {'override' if d.overrodeFindingId else 'accepted'} · marks {d.score if d.score is not None else '—'} · {d.note}", ""]
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
