"""The repo map: what every investigation used to spend its first three tool calls discovering.

Built once per submission, deterministically, and handed to each agent as its *first tool result*
(see `verify/tools.py: ToolBox.seed`). It is still submission content, so it travels wrapped in
`<untrusted>` inside a tool-result message — never in a user turn (docs/05 boundary 1).

Contents: the file tree, the dependency manifest, and a symbol map — the lines that declare
routes, classes, security annotations and tests, found by grep rather than by a model. A model
that starts from this makes its first real call on the right file.
"""

from __future__ import annotations

import re
from pathlib import Path

from repoman.probes import read_text, rel, walk
from repoman.probes.deps import PARSERS

TREE_CAP = 250
SYMBOL_CAP = 160
LINE_CHARS = 140
CODE = {".java", ".kt", ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".rs", ".cs", ".php", ".scala"}

# ponytail: one regex per family of "declaration that an evaluator cares about"; not a parser
SYMBOL = re.compile(
    r"^\s*(?:"
    r"@(?:Rest)?Controller|@(?:Get|Post|Put|Delete|Patch|Request)Mapping|@PreAuthorize|@Secured|@RolesAllowed"
    r"|@Cacheable|@EnableCaching|@Scheduled|@Entity|@Test|@SpringBootTest|@WebMvcTest"
    r"|(?:public|private|protected)?\s*(?:abstract\s+|final\s+)?(?:class|interface|enum|record)\s+\w+"
    r"|(?:async\s+)?def\s+\w+|class\s+\w+|@(?:app|router|bp|api)\.\w+\(|@pytest|def\s+test_"
    r"|(?:app|router|server)\.(?:get|post|put|delete|patch|use)\(|export\s+(?:default\s+)?(?:async\s+)?(?:function|class)\s+\w+"
    r"|(?:describe|it|test)\(|func\s+(?:\(\w+ \*?\w+\)\s*)?\w+|fn\s+\w+|#\[(?:test|get|post|route)"
    r")",
    re.M,
)


def build(root: Path, quarantined: frozenset[str] = frozenset()) -> str:
    files = [p for p in walk(root) if rel(root, p) not in quarantined]
    lines = ["## tree"]
    for p in files[:TREE_CAP]:
        lines.append(rel(root, p))
    if len(files) > TREE_CAP:
        lines.append(f"… {len(files) - TREE_CAP} more files; use tree(path) on a subdirectory")

    for p in files:
        parser = PARSERS.get(p.name)
        if parser:
            names = [f"{name} ({rel(root, p)}:{line})" for name, line in parser(read_text(p))]
            lines += [f"## dependencies declared in {rel(root, p)}", *names[:80]]

    lines.append("## symbols  (path:line: declaration — routes, classes, security, caching, tests)")
    n = 0
    for p in files:
        if p.suffix.lower() not in CODE:
            continue
        text = read_text(p)
        if not text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if SYMBOL.match(line):
                n += 1
                if n <= SYMBOL_CAP:
                    lines.append(f"{rel(root, p)}:{i}: {line.strip()[:LINE_CHARS]}")
    if n > SYMBOL_CAP:
        lines.append(f"… {n - SYMBOL_CAP} more declarations; grep for what you need")
    if n == 0:
        lines.append("(no declarations matched; use grep)")
    return "\n".join(lines)


if __name__ == "__main__":
    sample = Path(__file__).resolve().parents[2] / "fixtures" / "sample_run" / "repo"
    m = build(sample)
    assert "## tree" in m and "pom.xml" in m and "## symbols" in m
    assert "UserRole.java" in m and "@PreAuthorize" in m and "@Test" in m, m
    assert "README.md" not in build(sample, frozenset({"README.md"}))
    print(f"repomap ok ({len(m)} chars)")
