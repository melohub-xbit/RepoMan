"""Liveness of the deployed URL: status, title, timestamp.

v1 runs nothing from the submission (docs/05 boundary 3), so this is the only "does it actually
work" signal available — a cheap stand-in for the sandboxed build that was deliberately cut.

The client is a parameter because a probe that needs the network takes one (AGENTS.md), which is
what keeps this testable without touching the internet. The URL comes from the evaluator's form
via the `Submission`, never from a model and never from the submission's own content.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from repoman.core.types import Evidence, HttpCapture, Submission
from repoman.probes import ProbeResult

TIMEOUT = 15.0
MAX_BODY = 200_000  # enough for a <title>; a 40MB SPA bundle is not worth reading

TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def probe(sub: Submission, client=None) -> ProbeResult:
    url = next((a.uri for a in sub.artifacts if a.kind == "deploy"), None)
    if not url:
        return ProbeResult(summary="No deployed URL was submitted.", data={"checked": False})

    owns_client = client is None
    if owns_client:
        import httpx

        client = httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                              headers={"User-Agent": "RepoMan/0.1 (evidence probe)"})
    try:
        response = client.get(url)
        status = response.status_code
        body = response.text[:MAX_BODY]
    except Exception as e:
        # Unreachable is a finding, not a crash. The evaluator sees "we tried, here is what happened".
        return ProbeResult(summary=f"{url} did not respond ({type(e).__name__}).",
                           data={"checked": True, "url": url, "reachable": False,
                                 "error": type(e).__name__})
    finally:
        if owns_client:
            client.close()

    match = TITLE.search(body)
    title = re.sub(r"\s+", " ", match.group(1)).strip()[:120] if match else None
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    evidence = [Evidence(
        submissionId=sub.id, provenance="probe", probeId="deploy",
        quote=f"<title>{title}</title>" if title else f"HTTP {status}",
        locator=HttpCapture(url=url, status=status, capturedAt=captured_at, title=title),
    )]
    live = 200 <= status < 400
    summary = f"{url} · {status}" + (f' · "{title}"' if title else "") + f" · {captured_at}"
    if not live:
        summary += " · not serving"
    return ProbeResult(summary=summary, evidence=evidence, data={
        "checked": True, "url": url, "reachable": True, "status": status, "title": title,
        "live": live, "capturedAt": captured_at,
    })
