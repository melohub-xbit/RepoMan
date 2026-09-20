"""C++ language-feature probe: which constructs the student actually used, with a line for each.

Built for the TA case: sixty single-file submissions off one course skeleton, and a rubric that says
"uses inheritance, virtual dispatch, at least three classes, no raw new/delete, RAII". Those are
greppable facts, so this probe finds them deterministically and the model is only asked about
judgement-shaped lines ("does the hierarchy model the domain").

Skeleton-aware: when the batch carries the assignment's skeleton (`baseline`), a feature only counts
where the student wrote it — a line that appears verbatim in the skeleton is the course's, not theirs.
Leftover scaffolding markers ("write the getter … below", "dummy return") are reported as unfinished
parts, which is the single most useful TA signal in this probe.

No compilation here. `build.py` does that, and only when the evaluator opts in.
"""

from __future__ import annotations

import re
from pathlib import Path

from repoman.core.types import Submission
from repoman.probes import ProbeResult, line_evidence, read_text, walk

CPP_SUFFIXES = {".cpp", ".cc", ".cxx", ".c++", ".h", ".hpp", ".hh", ".hxx", ".ipp"}
MAX_EVIDENCE_PER_FEATURE = 3

# (feature id, human label, regex on one line). One regex per feature; a parser is not needed to
# tell an evaluator "virtual appears on line 87 and 94 in code the student wrote".
FEATURES: list[tuple[str, str, re.Pattern[str]]] = [
    ("classes", "class or struct definitions", re.compile(r"^\s*(?:class|struct)\s+\w+\s*(?::|\{|$)")),
    ("inheritance", "inheritance (`: public Base`)", re.compile(r"^\s*(?:class|struct)\s+\w+\s*:\s*(?:public|protected|private)\s+\w+")),
    ("virtual", "virtual functions", re.compile(r"^\s*virtual\s+[\w:<>,\s*&]+\(")),
    ("pure_virtual", "pure virtual functions (abstract classes)", re.compile(r"virtual\s+[\w:<>,\s*&]+\([^)]*\)\s*(?:const\s*)?=\s*0\s*;")),
    ("override", "`override` on overriding functions", re.compile(r"\)\s*(?:const\s*)?override\b")),
    ("virtual_dtor", "virtual destructors", re.compile(r"virtual\s+~\w+\s*\(")),
    ("encapsulation", "private/protected members", re.compile(r"^\s*(?:private|protected)\s*:")),
    ("templates", "templates", re.compile(r"^\s*template\s*<")),
    ("operator_overload", "operator overloading", re.compile(r"\boperator\s*(?:[-+*/%<>=!&|^~\[\]()]+|<<|>>)\s*\(")),
    ("smart_pointers", "smart pointers (RAII)", re.compile(r"\b(?:std::)?(?:unique_ptr|shared_ptr|weak_ptr|make_unique|make_shared)\b")),
    ("raw_new", "raw `new`", re.compile(r"(?<![\w.])new\s+(?:\(|\w)")),
    ("raw_delete", "raw `delete`", re.compile(r"(?<![\w.])delete(?:\s*\[\s*\])?\s+\w")),
    ("exceptions", "exceptions (try/catch/throw)", re.compile(r"^\s*(?:try\b|catch\s*\(|throw\b)")),
    ("stl_containers", "STL containers", re.compile(r"\b(?:std::)?(?:vector|map|unordered_map|set|unordered_set|list|deque|array|stack|queue|priority_queue)\s*<")),
    ("stl_algorithms", "STL algorithms", re.compile(r"\b(?:std::)?(?:sort|find|find_if|for_each|transform|accumulate|count_if|any_of|all_of|remove_if)\s*\(")),
    ("const_ref_params", "const-reference parameters", re.compile(r"\(\s*[^)]*const\s+[\w:<>]+\s*&\s*\w+")),
    ("static_members", "static members", re.compile(r"^\s*static\s+(?!void\s+main)[\w:<>]+\s+\w+")),
    ("enum_class", "scoped enums (`enum class`)", re.compile(r"^\s*enum\s+class\s+\w+")),
    ("lambdas", "lambdas", re.compile(r"\[[^\]]*\]\s*\([^)]*\)\s*(?:->\s*[\w:<>]+\s*)?\{")),
    ("using_namespace_std", "`using namespace std;`", re.compile(r"^\s*using\s+namespace\s+std\s*;")),
    ("c_arrays", "C-style arrays", re.compile(r"^\s*(?:int|char|double|float|long|bool|string|std::string)\s+\w+\s*\[\s*\w*\s*\]")),
    ("c_io", "C stdio (`printf`/`scanf`)", re.compile(r"\b(?:printf|scanf|gets|puts)\s*\(")),
]

# Scaffolding a course skeleton leaves for the student. Still present = part not attempted.
TODO_MARKERS = re.compile(
    r"//\s*(?:TODO|FIXME|write\s+(?:the\s+)?\w+.*?\bbelow|delete\s+the\s+dummy|implement\s+(?:this|the)\b|your\s+code\s+here|complete\s+(?:this|the)\b)"
    r"|/\*.*?(?:TODO|your code here|dummy).*?\*/",
    re.I,
)


def _normalise(line: str) -> str:
    """Skeleton matching ignores all whitespace: `class Story: public Post{` and `class Story : public Post {`
    are the same line typed by two students' editors."""
    return re.sub(r"\s+", "", line)


def student_lines(text: str, baseline: str | None) -> set[int]:
    """1-indexed lines that are not verbatim in the skeleton. Without a skeleton, every line is the student's."""
    lines = text.splitlines()
    if not baseline:
        return set(range(1, len(lines) + 1))
    skeleton = {_normalise(ln) for ln in baseline.splitlines() if _normalise(ln)}
    return {i for i, ln in enumerate(lines, start=1) if _normalise(ln) and _normalise(ln) not in skeleton}


def derive_baseline(sources: list[str], share: float = 0.8) -> str:
    """The skeleton, recovered from the cohort: lines present in at least `share` of the submissions.

    Sixty students started from the same file; what they all still have in common is what they were given.
    Used when the evaluator has not uploaded the skeleton itself."""
    if len(sources) < 3:
        return ""
    from collections import Counter

    counter: Counter[str] = Counter()
    originals: dict[str, str] = {}
    for text in sources:
        seen = set()
        for line in text.splitlines():
            key = _normalise(line)
            if key and key not in seen:
                seen.add(key)
                originals.setdefault(key, line)
        counter.update(seen)
    return "\n".join(originals[k] for k, c in counter.items() if c >= share * len(sources))


def probe(repo: Path, sub: Submission, baseline: str | None = None) -> ProbeResult:
    files = [p for p in walk(repo, CPP_SUFFIXES)]
    if not files:
        return ProbeResult(summary="No C++ source files found.", data={"files": 0})

    counts: dict[str, int] = {f: 0 for f, _, _ in FEATURES}
    evidence = []
    per_feature_ev: dict[str, int] = {f: 0 for f, _, _ in FEATURES}
    todos: list[tuple[Path, int, str]] = []
    total_lines = 0
    student_total = 0
    class_names: set[str] = set()

    for path in files:
        text = read_text(path)
        if not text:
            continue
        lines = text.splitlines()
        total_lines += len(lines)
        theirs = student_lines(text, baseline)
        student_total += len(theirs)
        for i, line in enumerate(lines, start=1):
            if TODO_MARKERS.search(line):  # wherever it is: a marker the skeleton planted and the student left is the point
                todos.append((path, i, line.strip()))
            if i not in theirs:
                continue  # the skeleton's line, not the student's
            for fid, _label, rx in FEATURES:
                if rx.search(line):
                    counts[fid] += 1
                    if fid == "classes":
                        m = re.match(r"^\s*(?:class|struct)\s+(\w+)", line)
                        if m:
                            class_names.add(m.group(1))
                    if per_feature_ev[fid] < MAX_EVIDENCE_PER_FEATURE:
                        evidence.append(line_evidence(repo, path, i, line.strip()[:200], sub, "cpp"))
                        per_feature_ev[fid] += 1

    for path, i, line in todos[:MAX_EVIDENCE_PER_FEATURE]:
        evidence.append(line_evidence(repo, path, i, line[:200], sub, "cpp"))

    used = [label for fid, label, _ in FEATURES if counts[fid] and fid not in ("raw_new", "raw_delete", "using_namespace_std", "c_arrays", "c_io")]
    parts = [f"{len(files)} C++ file{'s' if len(files) != 1 else ''}, {total_lines} lines"
             + (f" ({student_total} written by the student)" if baseline else ""),
             f"{len(class_names)} class{'es' if len(class_names) != 1 else ''} defined" + (f" ({', '.join(sorted(class_names)[:6])}{'…' if len(class_names) > 6 else ''})" if class_names else "")]
    if used:
        parts.append("uses " + ", ".join(used[:8]) + ("…" if len(used) > 8 else ""))
    if counts["raw_new"] or counts["raw_delete"]:
        parts.append(f"raw new/delete: {counts['raw_new']}/{counts['raw_delete']}" + (" with smart pointers alongside" if counts["smart_pointers"] else ", no smart pointers"))
    if todos:
        parts.append(f"{len(todos)} scaffolding marker{'s' if len(todos) != 1 else ''} still present (unfinished parts)")
    if not counts["encapsulation"] and counts["classes"]:
        parts.append("no private/protected members")

    return ProbeResult(
        summary=" · ".join(parts) + ".",
        evidence=evidence,
        data={"files": len(files), "lines": total_lines, "studentLines": student_total if baseline else None,
              "classes": sorted(class_names), "counts": counts, "todos": len(todos),
              "skeletonAware": bool(baseline)},
    )
