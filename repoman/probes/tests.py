"""Do tests exist, in what framework, how many — and how many actually assert anything.

Counting test files is easy and nearly meaningless; a generated `contextLoads()` is a test file
and proves nothing. So this probe also counts tests whose body contains no assertion, which is
the number an evaluator actually wants when a rubric says "comprehensive test suite".

v1 never *runs* a test suite (docs/05 boundary 3). This is a static read.
"""

from __future__ import annotations

import re
from pathlib import Path

from repoman.core.types import Submission
from repoman.probes import ProbeResult, line_evidence, read_text, rel, walk

TEST_SUFFIXES = {".java", ".kt", ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".rs", ".cs", ".php"}

ASSERTION = re.compile(r"\b(assert\w*|Assert\w*|expect|should\w*|verify|require|EXPECT_\w+|"
                       r"assertThat|willReturn|andExpect)\b|\.to\w*\(", re.I)

# (language, how a test declaration looks, how the framework announces itself)
DIALECTS = [
    ("java", re.compile(r"^\s*@Test\b", re.M), {
        "JUnit 5": "org.junit.jupiter", "JUnit 4": "org.junit.Test", "TestNG": "org.testng"}),
    ("kotlin", re.compile(r"^\s*@Test\b", re.M), {"JUnit 5": "org.junit.jupiter", "JUnit 4": "org.junit.Test"}),
    ("python", re.compile(r"^\s*(?:async\s+)?def\s+test\w*\s*\(", re.M), {
        "pytest": "import pytest", "unittest": "import unittest"}),
    ("go", re.compile(r"^\s*func\s+Test\w*\s*\(", re.M), {"go test": "testing"}),
    ("js", re.compile(r"^\s*(?:it|test)\s*(?:\.\w+)?\s*\(", re.M), {
        "Vitest": "vitest", "Jest": "@jest", "Mocha": "mocha", "Jasmine": "jasmine"}),
    ("ruby", re.compile(r"^\s*(?:it|def test_)\b", re.M), {"RSpec": "rspec", "minitest": "minitest"}),
    ("csharp", re.compile(r"^\s*\[(?:Test|Fact|Theory)\]", re.M), {"xUnit": "Xunit", "NUnit": "NUnit"}),
]

LANG_BY_SUFFIX = {".java": "java", ".kt": "kotlin", ".py": "python", ".go": "go", ".rb": "ruby",
                  ".cs": "csharp", ".js": "js", ".jsx": "js", ".ts": "js", ".tsx": "js"}


def looks_like_a_test_file(path: Path) -> bool:
    name = path.name.lower()
    parts = {p.lower() for p in path.parts}
    return (
        name.startswith("test_") or name.endswith(("_test.py", "_test.go", "_test.rb", "_spec.rb"))
        or re.search(r"(test|tests|spec)\.[jt]sx?$", name) is not None
        or re.search(r"(test|tests)\.(java|kt|cs)$", name) is not None
        or bool(parts & {"test", "tests", "__tests__", "spec", "specs"})
    )


def probe(repo: Path, sub: Submission) -> ProbeResult:
    files = [p for p in walk(repo, TEST_SUFFIXES) if looks_like_a_test_file(p)]
    if not files:
        return ProbeResult(summary="No test files found.", data={"files": 0, "tests": 0, "withoutAssertion": 0})

    frameworks: set[str] = set()
    total = 0
    silent: list[tuple[Path, int, str]] = []  # tests whose body asserts nothing
    loud: list[tuple[Path, int, str]] = []
    per_file: dict[str, int] = {}

    for path in files:
        text = read_text(path)
        if not text:
            continue
        lang = LANG_BY_SUFFIX.get(path.suffix.lower())
        pattern, markers = next(((p, m) for name, p, m in DIALECTS if name == lang), (None, {}))
        if pattern is None:
            continue
        for label, needle in markers.items():
            if needle in text:
                frameworks.add(label)
                break

        starts = [m.start() for m in pattern.finditer(text)]
        per_file[rel(repo, path)] = len(starts)
        total += len(starts)
        bounds = starts + [len(text)]
        for i, start in enumerate(starts):
            body = text[start:bounds[i + 1]]
            line_no = text.count("\n", 0, start) + 1
            label = _test_name(body) or f"line {line_no}"
            (loud if ASSERTION.search(body) else silent).append((path, line_no, label))

    evidence = []
    for path, line_no, _label in silent[:4]:
        lines = read_text(path).splitlines()
        quote = "\n".join(lines[line_no - 1: line_no + 2]).strip()
        evidence.append(line_evidence(repo, path, line_no, quote, sub, "tests",
                                      end_line=min(line_no + 2, len(lines))))
    if not silent and loud:
        path, line_no, _ = loud[0]
        lines = read_text(path).splitlines()
        evidence.append(line_evidence(repo, path, line_no, "\n".join(lines[line_no - 1: line_no + 2]).strip(),
                                      sub, "tests", end_line=min(line_no + 2, len(lines))))

    fw = " + ".join(sorted(frameworks)) if frameworks else "unrecognised framework"
    summary = (f"{fw} · {len(files)} file{'' if len(files) == 1 else 's'} · "
               f"{total} test{'' if total == 1 else 's'}")
    if silent:
        summary += f" · {len(silent)} with no assertion"
    return ProbeResult(summary=summary + ".", evidence=evidence, data={
        "files": len(files), "tests": total, "withoutAssertion": len(silent),
        "frameworks": sorted(frameworks), "perFile": per_file,
        "silentNames": [label for _, _, label in silent[:20]],
    })


def _test_name(body: str) -> str | None:
    m = re.search(r"(?:void|def|func)\s+(\w+)\s*\(", body) or re.search(r"""^\s*(?:it|test)\s*\(\s*['"]([^'"]+)""", body)
    return m.group(1) if m else None
