"""`repoman run <repo-or-zip> --rubric r.md` — Track 1, and the Day 1 exit criterion.

This is not a lesser version of the web UI. It is the procurement argument: with
`REPOMAN_OLLAMA_HOST` set, the model is local, the store is local, and no byte of a submission
leaves the machine. For an institution with student-records obligations, that sentence ends the
conversation.

The exit criterion for the spine is here: a permalink printed by this command must open to the
lines it names.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from repoman.core.types import Batch, Finding, Rubric
from repoman.store import LocalStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="repoman", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="investigate one submission against a rubric")
    run.add_argument("target", help="a GitHub repository URL or a path to a zip")
    run.add_argument("--rubric", help="rubric file: prose, markdown or an approved rubric.json "
                                      "(not needed with --batch, which reuses the batch's rubric)")
    run.add_argument("--report", help="the submission's report or deck as a PDF")
    run.add_argument("--deploy", help="the submission's deployed URL")
    run.add_argument("--batch", help="add to an existing batch id, reusing its approved rubric — "
                                     "required for cross-submission similarity, which compares within a batch")
    run.add_argument("--from", dest="window_start", help="event window start, e.g. 2026-09-18")
    run.add_argument("--until", dest="window_end", help="event window end")
    run.add_argument("--data", default=os.environ.get("REPOMAN_DATA", "data"), help="where to write the run")
    run.add_argument("--json", action="store_true", help="print findings as JSON instead of a table")
    run.add_argument("--no-claims", action="store_true", help="skip checking what the submission claims about itself")

    bulk = sub.add_parser("bulk", help="investigate many submissions from one archive or directory of submissions")
    bulk.add_argument("target", help="a .zip or a directory: one top-level folder (or loose file) per submission — "
                      "the shape DOMjudge, Moodle and GitHub Classroom export as-is")
    bulk.add_argument("--rubric", help="rubric file, as in `run`")
    bulk.add_argument("--batch", help="add to an existing batch id, reusing its approved rubric")
    bulk.add_argument("--skeleton", help="the assignment's starter file; feature probes then count only "
                      "what each student wrote. Without one, RepoMan derives it from what the batch shares.")
    bulk.add_argument("--from", dest="window_start", help="event window start, e.g. 2026-09-18")
    bulk.add_argument("--until", dest="window_end", help="event window end")
    bulk.add_argument("--blind", action="store_true", help="strip names/labels before each run")
    bulk.add_argument("--workers", type=int, default=2, help="submissions investigated at once (default 2 — "
                      "the model provider throttles the whole account, not per submission)")
    bulk.add_argument("--data", default=os.environ.get("REPOMAN_DATA", "data"))
    bulk.add_argument("--no-claims", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "run" and not args.rubric and not args.batch:
        parser.error("give --rubric, or --batch to reuse a batch's approved rubric")
    if args.command == "bulk" and not args.rubric and not args.batch:
        parser.error("give --rubric, or --batch to reuse a batch's approved rubric")
    return {"run": _run, "bulk": _bulk}.get(args.command, lambda _a: 2)(args)


def _run(args) -> int:
    from repoman import pipeline  # imported here so --help works without a model configured

    store = LocalStore(args.data)

    if args.batch:
        # Joining a batch means inheriting its approved rubric. Re-compiling per submission would
        # grade the cohort against slightly different checklists, which is the opposite of the point.
        raw = store.get_json(f"batches/{args.batch}.json")
        if not raw:
            raise SystemExit(f"no batch {args.batch} in {args.data}")
        batch = Batch.model_validate(raw)
        rubric = Rubric.model_validate(store.get_json(f"rubrics/{batch.rubricId}.json"))
        print(f"Adding to batch {batch.name} ({len(batch.runIds)} submission(s), rubric v{rubric.version})",
              file=sys.stderr)
    else:
        rubric = _load_rubric(Path(args.rubric), store)
        batch = Batch(name="cli", rubricId=rubric.id,
                      eventWindow=(args.window_start, args.window_end)
                      if args.window_start and args.window_end else None)
        store.put_json(f"rubrics/{rubric.id}.json", rubric)
    store.put_json(f"batches/{batch.id}.json", batch)

    is_zip = not args.target.startswith(("http://", "https://", "git@"))
    print(f"Investigating {args.target}", file=sys.stderr)

    run_id = pipeline.run_submission(
        store, batch.id, rubric,
        repo_url=None if is_zip else args.target,
        zip_path=args.target if is_zip else None,
        report_path=args.report, deploy_url=args.deploy, event_window=batch.eventWindow,
        check_claims=not args.no_claims,
    )

    # Link the run to its batch, so a CLI run is a first-class row in the web UI rather than an
    # orphan on disk: same store, same batch page, same exports.
    batch.runIds.append(run_id)
    store.put_json(f"batches/{batch.id}.json", batch)

    findings = [Finding.model_validate(f) for f in store.get_json(f"runs/{run_id}/findings.json", [])]
    submission = store.get_json(f"runs/{run_id}/submission.json", {})
    manifest = store.get_json(f"runs/{run_id}/manifest.json", {})

    if args.json:
        print(json.dumps([f.model_dump() for f in findings], indent=2))
        return 0

    _print_table(findings, rubric, submission, manifest, run_id, Path(args.data))
    _print_similarity(store, batch, run_id)
    print(f"  Batch {batch.id} — add another with: repoman run <target> --batch {batch.id}")
    return 0


def _bulk(args) -> int:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from repoman import pipeline
    from repoman.probes import cpp

    store = LocalStore(args.data)
    if args.batch:
        raw = store.get_json(f"batches/{args.batch}.json")
        if not raw:
            raise SystemExit(f"no batch {args.batch} in {args.data}")
        batch = Batch.model_validate(raw)
        rubric = Rubric.model_validate(store.get_json(f"rubrics/{batch.rubricId}.json"))
    else:
        rubric = _load_rubric(Path(args.rubric), store)
        batch = Batch(name=f"bulk:{Path(args.target).name}", rubricId=rubric.id, blind=args.blind,
                      eventWindow=(args.window_start, args.window_end) if args.window_start and args.window_end else None)
        store.put_json(f"rubrics/{rubric.id}.json", rubric)

    target = Path(args.target)
    # Same reason as the web handler: never write under "batches/<id>/...", which
    # store.list("batches") globs recursively for Batch JSON.
    holding = store.local_dir(f"imports/{batch.id}/cli") if target.is_file() else target
    entries = pipeline.expand_bulk_import(target, holding)
    if not entries:
        raise SystemExit(f"nothing to import in {target}")

    if args.skeleton:
        batch.baseline = Path(args.skeleton).read_text(encoding="utf-8", errors="replace")
    elif not batch.baseline:
        sources = [f.read_text(encoding="utf-8", errors="replace") for d, _ in entries for f in d.rglob("*") if f.suffix in cpp.CPP_SUFFIXES]
        derived = cpp.derive_baseline(sources)
        if derived:
            batch.baseline = derived
            print(f"Derived a {len(derived.splitlines())}-line skeleton from {len(entries)} submissions.", file=sys.stderr)

    print(f"Investigating {len(entries)} submissions from {target} (batch {batch.id}, {args.workers} at a time)", file=sys.stderr)
    store.put_json(f"batches/{batch.id}.json", batch)

    def one(job):
        d, label = job
        run_id = pipeline.run_submission(store, batch.id, rubric, dir_path=str(d), event_window=batch.eventWindow,
                                         check_claims=not args.no_claims, baseline=batch.baseline,
                                         label=None if batch.blind else label)
        return run_id, label

    rows = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(one, job): job for job in entries}
        for fut in as_completed(futures):
            try:
                run_id, label = fut.result()
            except Exception as e:
                print(f"  ✗ {futures[fut][1]}: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            batch.runIds.append(run_id)
            findings = [Finding.model_validate(f) for f in store.get_json(f"runs/{run_id}/findings.json", [])]
            from repoman.core.types import coverage

            v, n = coverage(findings, [r for r in rubric.requirements if r.verifiable])
            flags = sorted({fl for f in findings for fl in f.flagged})
            rows.append((label if not batch.blind else run_id[:6], v, n, flags))
            print(f"  {v} of {n}  {label if not batch.blind else 'S-' + run_id[:6]}" + (f"  [{', '.join(flags)}]" if flags else ""))
    store.put_json(f"batches/{batch.id}.json", batch)

    print(f"\n{len(rows)} submissions investigated. Batch {batch.id} — open it in the web UI, or add more with:")
    print(f"  repoman bulk <archive-or-dir> --batch {batch.id}")
    return 0


def _print_similarity(store: LocalStore, batch: Batch, run_id: str) -> None:
    """Shared code with the rest of the batch. Silent when there is nothing to report."""
    from repoman.pipeline import batch_similarity
    from repoman.probes.similarity import fingerprint  # noqa: F401  (kept explicit for readers)

    overlaps = [o for o in batch_similarity(store, batch.runIds) if run_id in (o["a"], o["b"])]
    if not overlaps:
        return
    print()
    for o in overlaps:
        other = o["b"] if o["a"] == run_id else o["a"]
        sub = store.get_json(f"runs/{other}/submission.json", {}) or {}
        name = (sub.get("repoUrl") or other).replace("https://github.com/", "")
        print(f"  Shared code: {o['sharedCount']} file(s) identical to {name} "
              f"({round(o['share'] * 100)}% of the smaller submission)")
        for path in o["shared"][:5]:
            print(f"    {path}")


def _load_rubric(path: Path, store: LocalStore) -> Rubric:
    if not path.is_file():
        raise SystemExit(f"rubric not found: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return Rubric.model_validate(json.loads(text))  # already compiled and approved
    from repoman.verify.compile import compile_rubric

    print("Compiling the rubric…", file=sys.stderr)
    rubric = compile_rubric(text, ["repo", "readme", "report", "deploy"])
    for r in rubric.requirements:
        mark = "·" if r.verifiable else "—"
        note = "" if r.verifiable else f"  (stays with you: {r.unverifiableReason})"
        print(f"  {mark} {r.title} [{r.weight:g} marks]{note}", file=sys.stderr)
    return rubric


def _print_table(findings, rubric, submission, manifest, run_id, data_dir: Path) -> None:
    by_req = {f.requirementId: f for f in findings}
    repo_url = submission.get("repoUrl")
    sha = submission.get("commitSha", "")

    width = max((len(r.title) for r in rubric.requirements), default=12)
    print()
    for req in rubric.requirements:
        finding = by_req.get(req.id)
        if not req.verifiable:
            print(f"  {req.title:<{width}}  {'STAYS WITH YOU':<14}  {req.unverifiableReason or ''}")
            continue
        if finding is None:
            print(f"  {req.title:<{width}}  {'NOT CHECKED':<14}")
            continue
        state = "LOOKED · NONE" if finding.state == "UNVERIFIED" else finding.state
        print(f"  {req.title:<{width}}  {state:<14}  {_permalink(finding, repo_url, sha)}")
        print(f"  {'':<{width}}  {'':<14}  {finding.summary}")
        for ev in finding.evidence[1:4]:
            print(f"  {'':<{width}}  {'':<14}  {_locator(ev.locator, repo_url, sha)}")
        print()

    verifiable = [r for r in rubric.requirements if r.verifiable]
    verified = sum(1 for f in findings if f.state == "VERIFIED")
    print(f"  {verified} of {len(verifiable)} requirements have verified evidence. "
          f"RepoMan does not score; that part is yours.")
    if manifest.get("mismatches"):
        print(f"  {manifest['mismatches']} citation(s) did not resolve and were dropped.")
    for note in manifest.get("notes") or []:
        print(f"  Note: {note}")
    usage = manifest.get("usage") or []
    if usage:
        tokens = sum(u.get("inputTokens", 0) + u.get("outputTokens", 0) for u in usage)
        print(f"  {tokens:,} tokens across {len(usage)} requirement passes.")
    print(f"  Run written to {data_dir / 'runs' / run_id}")


def _permalink(finding: Finding, repo_url: str | None, sha: str) -> str:
    return _locator(finding.evidence[0].locator, repo_url, sha) if finding.evidence else ""


def _locator(loc, repo_url: str | None, sha: str) -> str:
    if loc.kind == "file_range":
        if repo_url:
            base = repo_url.rstrip("/").removesuffix(".git")
            return f"{base}/blob/{loc.commitSha or sha}/{loc.path}#L{loc.startLine}-L{loc.endLine}"
        return f"{loc.path}:{loc.startLine}-{loc.endLine}"
    if loc.kind == "doc_span":
        return f"report p.{loc.page}"
    if loc.kind == "http_capture":
        return f"{loc.url} → {loc.status}"
    return f"commit {loc.commitSha[:10]}"


if __name__ == "__main__":
    raise SystemExit(main())
