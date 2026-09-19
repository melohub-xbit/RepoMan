"""The six tools the agent may call. All read-only, all over a checkout that already exists.

There is no index. The agent searches the repository the way a TA would — `tree`, `grep`,
`read_file` — and cites what it finds. That decision (docs/02) removed tree-sitter, embeddings,
a vector store and a retrieval-tuning loop from the build.

Three properties are not negotiable:

- **No tool takes a URL, writes a file, or spawns a process.** Deterministic code decided what to
  acquire, before any model ran. The model only decides what to *read* (docs/05 boundary 2).
- **Every result is wrapped in `<untrusted>`.** Submitted content reaches the model as data, never
  as instruction, and the system prompt tells it to report anything that tries to be one.
- **Caps are enforced here, not requested in the prompt.** Truncation is always visible in the
  result, so the finding can honestly record how far the search got.
- **Every file, page and probe result passes through the Cedar policy** in `policy.cedar` before
  it is read. Quarantine, path escape and vendored trees are denials by policy, not by luck.
"""

from __future__ import annotations

import fnmatch
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from strands import tool

from repoman.probes import VENDORED, read_text
from repoman.verify.policy import allowed

TREE_ENTRIES = 400
GREP_MATCHES = 60
READ_LINES = 200
GREP_LINE_CHARS = 240


def untrusted(source: str, body: str) -> str:
    """Wrap submission content so the model can tell it apart from its instructions."""
    return f'<untrusted source="{source}">\n{body}\n</untrusted>'


@dataclass
class ToolBox:
    """The tools for one submission, closed over its checkout.

    `calls` is the tool-call budget. It is counted here rather than trusted to the prompt, and
    what it produces — `searchExhausted` — is reported to the evaluator rather than hidden.
    """

    root: Path
    pages: dict[str, str] = field(default_factory=dict)
    probe_summary: str = ""
    quarantined: frozenset[str] = frozenset()
    cap: int = 12
    calls: int = 0
    read_paths: list[str] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)  # "action resource" for every request the policy refused
    repo_map: str = ""  # probes/repomap.build(): tree + dependencies + symbols, computed once per submission

    def __post_init__(self) -> None:
        # Resolve once so every path comparison below is between absolute paths.
        self.root = self.root.resolve()

    @property
    def exhausted(self) -> bool:
        return self.calls >= self.cap

    def _spend(self) -> str | None:
        self.calls += 1
        if self.calls > self.cap:
            return ("Tool-call budget spent. Write the finding with what you have; the finding will "
                    "record that the search was cut short.")
        return None

    def _permit(self, action: str, p: Path) -> bool:
        """Ask the policy about one file. Every attribute Cedar sees is computed here from the path."""
        inside = p == self.root or self.root in p.parents
        rel = self._rel(p) if inside else str(p)
        parts = set(p.relative_to(self.root).parts) if inside else set()
        ok = allowed(action, "File", rel, inside_checkout=inside, quarantined=rel in self.quarantined,
                     vendored=bool(parts & VENDORED) or any(x.startswith(".git") for x in parts))
        if not ok:
            self.denied.append(f"{action} {rel}")
        return ok

    def _safe(self, rel_path: str, action: str) -> Path | None:
        """A path the policy lets `action` touch, or None."""
        rel_path = (rel_path or "").strip().lstrip("/\\")
        try:
            p = (self.root / rel_path).resolve()
        except (OSError, ValueError):
            return None
        return p if self._permit(action, p) else None

    def _files(self, action: str):
        """Every file under the root that the policy lets `action` read. Vendored trees are not descended."""
        stack = [self.root]
        while stack:
            current = stack.pop()
            try:
                entries = sorted(current.iterdir())
            except OSError:
                continue
            for p in entries:
                if p.is_dir():
                    if p.name not in VENDORED and not p.name.startswith(".git"):
                        stack.append(p)
                elif self._permit(action, p):
                    yield p

    def _rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.root)).replace("\\", "/")
        except ValueError:
            return str(p)

    # --- the tools ---------------------------------------------------------------

    def build(self) -> list:
        """The six `@tool` callables, bound to this submission."""

        @tool
        def tree(path: str = ".") -> str:
            """List the files in the submission, with sizes. Vendored and build directories are pruned.

            Args:
                path: directory within the repository to list. Defaults to the repository root.
            """
            spent = self._spend()
            if spent:
                return spent
            base = self._safe(path, "tree")
            if base is None or not base.is_dir():
                return untrusted("tree", f"No directory {path!r} in this submission.")
            rows, truncated = [], False
            for i, p in enumerate(sorted(self._files("tree"), key=self._rel)):
                if not str(p).startswith(str(base)):
                    continue
                if len(rows) >= TREE_ENTRIES:
                    truncated = True
                    break
                try:
                    rows.append(f"{self._rel(p)} ({p.stat().st_size} b)")
                except OSError:
                    continue
            body = "\n".join(rows) or "(no files)"
            if truncated:
                body += f"\n… listing truncated at {TREE_ENTRIES} entries."
            return untrusted(f"tree:{path}", body)

        @tool
        def grep(pattern: str, glob: str = "*") -> str:
            """Search the submission's files for a regular expression.

            Args:
                pattern: a Python regular expression, applied case-insensitively.
                glob: restrict the search to files matching this glob, e.g. "*.java".
            """
            spent = self._spend()
            if spent:
                return spent
            try:
                rx = re.compile(pattern, re.I)
            except re.error as e:
                return untrusted("grep", f"Not a valid regular expression: {e}")
            out, truncated = [], False
            for p in self._files("grep"):
                rel = self._rel(p)
                if glob not in ("", "*", "**/*") and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(p.name, glob)):
                    continue
                text = read_text(p)
                if not text:
                    continue
                for n, line in enumerate(text.splitlines(), start=1):
                    if rx.search(line):
                        if len(out) >= GREP_MATCHES:
                            truncated = True
                            break
                        out.append(f"{rel}:{n}: {line.strip()[:GREP_LINE_CHARS]}")
                if truncated:
                    break
            body = "\n".join(out) or f"No match for {pattern!r}."
            if truncated:
                body += f"\n… stopped at {GREP_MATCHES} matches; narrow the pattern to see more."
            return untrusted(f"grep:{pattern}", body)

        @tool
        def read_file(path: str, start: int = 1, end: int | None = None) -> str:
            """Read numbered lines from one file. Cite the line numbers exactly as shown here.

            Args:
                path: path within the repository, as shown by tree or grep.
                start: first line to read, 1-indexed.
                end: last line to read. At most 200 lines are returned per call.
            """
            spent = self._spend()
            if spent:
                return spent
            p = self._safe(path, "read_file")
            if p is None or not p.is_file():
                where = "withheld: it carried a prompt-injection payload" if p is None and path in self.quarantined \
                    else "not found in this submission"
                return untrusted(f"file:{path}", f"{path} is {where}.")
            lines = read_text(p).splitlines()
            if not lines:
                return untrusted(f"file:{path}", "(empty or unreadable)")
            lo = max(1, start)
            hi = min(len(lines), (end or lo + READ_LINES - 1))
            hi = min(hi, lo + READ_LINES - 1)
            self.read_paths.append(self._rel(p))
            body = "\n".join(f"{n}: {lines[n - 1]}" for n in range(lo, hi + 1))
            if hi < len(lines):
                body += f"\n… file continues to line {len(lines)}."
            return untrusted(f"file:{path}", body)

        @tool
        def read_report_page(page: int) -> str:
            """Read one page of the submitted report or slide deck, as extracted text.

            Args:
                page: 1-indexed page number.
            """
            spent = self._spend()
            if spent:
                return spent
            if not self.pages:
                return untrusted("report", "No report or deck was submitted.")
            if not allowed("read_report_page", "Report", str(page), quarantined="report" in self.quarantined):
                self.denied.append(f"read_report_page {page}")
                return untrusted("report", "The report is quarantined and cannot be read.")
            text = self.pages.get(str(page))
            if text is None:
                return untrusted("report", f"The report has pages 1–{len(self.pages)}; there is no page {page}.")
            return untrusted(f"report:p{page}", text)

        @tool
        def list_deps() -> str:
            """The submission's declared dependencies, as parsed from its manifest."""
            spent = self._spend()
            if spent:
                return spent
            manifests = [p for p in self._files("list_deps")
                         if p.name in ("pom.xml", "package.json", "pyproject.toml", "requirements.txt",
                                       "go.mod", "build.gradle", "build.gradle.kts", "Cargo.toml", "Gemfile")]
            if not manifests:
                return untrusted("deps", "No dependency manifest found.")
            blocks = []
            for p in manifests:
                text = read_text(p)
                numbered = "\n".join(f"{n}: {line}" for n, line in enumerate(text.splitlines(), start=1))
                blocks.append(f"# {self._rel(p)}\n{numbered[:8000]}")
            return untrusted("deps", "\n\n".join(blocks))

        @tool
        def repo_map() -> str:
            """The whole-repository map: file tree, declared dependencies, and every route, class, security
            annotation and test declaration with its path and line. Already shown to you at the start."""
            if not allowed("repo_map", "Map", "repo"):
                self.denied.append("repo_map repo")
                return untrusted("repo_map", "Not available.")
            return untrusted("repo_map", self.repo_map or "No map was built for this submission.")

        @tool
        def probe_results() -> str:
            """What the deterministic probes found: dependencies, tests, git timeline, injection, deployment."""
            spent = self._spend()
            if spent:
                return spent
            # Our own sentences about the submission, not the submission's bytes — so this is the
            # one tool result that is not wrapped as untrusted.
            return self.probe_summary or "No probe results are available for this submission."

        return [repo_map, tree, grep, read_file, read_report_page, list_deps, probe_results]

    def seed(self) -> list[dict]:
        """The conversation's opening: a repo_map call and its result, already made.

        It is a tool-result message, so the map is submission content in the only place submission
        content is allowed (docs/05 boundary 1), and it costs no budget: the three orientation calls
        every investigation used to make are already answered when the model reads the requirement.
        """
        if not self.repo_map:
            return []
        tid = f"seed_{uuid.uuid4().hex[:12]}"
        return [
            {"role": "user", "content": [{"text": "Before the requirement, look at the map of this submission."}]},
            {"role": "assistant", "content": [{"toolUse": {"toolUseId": tid, "name": "repo_map", "input": {}}}]},
            {"role": "user", "content": [{"toolResult": {"toolUseId": tid, "status": "success",
                                                         "content": [{"text": untrusted("repo_map", self.repo_map)}]}}]},
            {"role": "assistant", "content": [{"text": "I have the map. Ready for the requirement."}]},
        ]
