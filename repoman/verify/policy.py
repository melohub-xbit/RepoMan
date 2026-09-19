"""The tool boundary as a Cedar policy, evaluated per call.

`policy.cedar` is the authority on what the verify agent may touch. `ToolBox` asks `allowed()` for
every file, page or probe result before reading it, so the boundary in docs/05 is a policy decision
with a file to point at — and a test that flips one attribute and watches the decision flip.
"""

from __future__ import annotations

from pathlib import Path

import cedarpy

POLICY_PATH = Path(__file__).with_name("policy.cedar")
POLICY = POLICY_PATH.read_text(encoding="utf-8")
PRINCIPAL = 'Agent::"verify"'


def allowed(action: str, kind: str, rid: str, *, inside_checkout: bool = True, vendored: bool = False,
            quarantined: bool = False) -> bool:
    """Cedar's answer for one request. Anything the policy does not permit is denied."""
    request = {"principal": PRINCIPAL, "action": f'Action::"{action}"', "resource": f'{kind}::"{rid}"', "context": {}}
    entities = [
        {"uid": {"type": "Agent", "id": "verify"}, "attrs": {}, "parents": []},
        {"uid": {"type": kind, "id": rid},
         "attrs": {"inside_checkout": inside_checkout, "vendored": vendored, "quarantined": quarantined},
         "parents": []},
    ]
    return cedarpy.is_authorized(request, POLICY, entities).decision == cedarpy.Decision.Allow


if __name__ == "__main__":
    assert allowed("read_file", "File", "src/App.java")
    assert not allowed("read_file", "File", "src/App.java", quarantined=True)
    assert not allowed("read_file", "File", "../etc/passwd", inside_checkout=False)
    assert not allowed("grep", "File", "node_modules/x.js", vendored=True)
    assert allowed("read_report_page", "Report", "3") and not allowed("read_report_page", "Report", "3", quarantined=True)
    assert not allowed("exec", "File", "build.sh") and not allowed("fetch", "Url", "https://x")
    assert not allowed("read_file", "Report", "1")  # the right tool for the right resource kind
    print("policy ok")
