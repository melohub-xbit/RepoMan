"""The Cedar policy is the tool boundary. Flip one attribute, watch the decision flip; then through the tools."""

from __future__ import annotations

from pathlib import Path

from repoman.verify.policy import POLICY_PATH, allowed
from repoman.verify.tools import ToolBox

REPO = Path(__file__).resolve().parent.parent / "fixtures" / "sample_run" / "repo"


def test_policy_file_is_the_authority():
    text = POLICY_PATH.read_text()
    assert "forbid" in text and 'Action::"exec"' in text and 'Action::"fetch"' in text
    assert allowed("read_file", "File", "src/App.java")
    assert not allowed("read_file", "File", "src/App.java", quarantined=True)
    assert not allowed("read_file", "File", "/etc/passwd", inside_checkout=False)
    assert not allowed("grep", "File", "node_modules/a.js", vendored=True)
    for action in ("exec", "fetch", "write"):
        assert not allowed(action, "File", "anything")


def test_tools_refuse_what_the_policy_denies():
    box = ToolBox(root=REPO, pages={"1": "page one"}, quarantined=frozenset({"README.md", "report"}))
    t = {x.tool_name: x for x in box.build()}
    assert "withheld" in t["read_file"]("README.md") and "TaskFlow" not in t["read_file"]("README.md")
    assert "README.md" not in t["tree"](".")
    assert "README" not in t["grep"]("TaskFlow")
    assert "quarantined" in t["read_report_page"](1)
    assert "pom.xml" in t["tree"](".") and "enum UserRole" in t["grep"]("enum UserRole")
    assert any(d.startswith("read_file README.md") for d in box.denied)
    assert any(d.startswith("read_report_page 1") for d in box.denied)


def test_path_escape_is_a_policy_denial():
    box = ToolBox(root=REPO)
    t = {x.tool_name: x for x in box.build()}
    out = t["read_file"]("../../pyproject.toml")
    assert "repoman" not in out and box.denied and "inside" not in box.denied[0]  # denied, and nothing leaked
