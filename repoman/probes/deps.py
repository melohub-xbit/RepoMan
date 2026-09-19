"""Declared dependencies versus ones the code actually imports.

The interesting signal is a dependency in the manifest that appears nowhere in the source: a
README claiming "we use Redis for caching" against a `pom.xml` entry nothing imports.

Being wrong here is expensive, so a dependency whose name yields no distinctive search token
(`spring-boot-starter-web` — "web" matches everything) is reported as **not checkable** rather
than guessed at. An honest "cannot tell" beats a confident false "unused".
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from repoman.core.types import Submission
from repoman.probes import ProbeResult, line_evidence, read_text, rel, walk

# A dependency that appears only in configuration is a different fact from one that appears
# nowhere — "declared and configured but never imported" is the honest reading of a Redis entry
# with a `spring.redis` block and no Java that touches it. The UI shows the difference.
CODE_SUFFIXES = {".java", ".kt", ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".rs", ".php",
                 ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".scala", ".m", ".vue", ".svelte"}
CONFIG_SUFFIXES = {".yml", ".yaml", ".toml", ".xml", ".json", ".properties", ".gradle", ".sql",
                   ".sh", ".env", ".cfg", ".ini", ".conf"}

# Tokens too common to prove anything by their presence in source.
GENERIC = {"spring", "boot", "starter", "data", "web", "core", "api", "test", "tests", "util",
           "utils", "common", "commons", "client", "server", "lib", "libs", "sdk", "java", "js",
           "node", "types", "plugin", "config", "tool", "tools", "app", "main", "dev", "build",
           "runtime", "compiler", "parser", "loader", "helper", "base", "std", "impl", "proj"}

MANIFESTS = ("pom.xml", "package.json", "pyproject.toml", "requirements.txt", "go.mod",
             "build.gradle", "build.gradle.kts", "Cargo.toml", "Gemfile")


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


# --- parsing each manifest into (name, declaration line) -------------------------


def _from_pom(text: str) -> list[tuple[str, int]]:
    out = []
    for block in re.finditer(r"<dependency>(.*?)</dependency>", text, re.S):
        m = re.search(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", block.group(1))
        if m:
            out.append((m.group(1), _line_of(text, block.start() + m.start(1))))
    return out


def _from_package_json(text: str) -> list[tuple[str, int]]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for section in ("dependencies", "peerDependencies"):
        for name in (data.get(section) or {}):
            m = re.search(rf'"{re.escape(name)}"\s*:', text)
            out.append((name, _line_of(text, m.start()) if m else 1))
    return out


def _from_pyproject(text: str) -> list[tuple[str, int]]:
    out = []
    for m in re.finditer(r'["\']([A-Za-z0-9._-]+)\s*(?:[<>=!~\[][^"\']*)?["\']', text):
        name = m.group(1)
        if name and not name.startswith("."):
            out.append((name, _line_of(text, m.start(1))))
    return out


def _from_requirements(text: str) -> list[tuple[str, int]]:
    out = []
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        name = re.split(r"[<>=!~\[;\s]", line, maxsplit=1)[0]
        if name:
            out.append((name, i))
    return out


def _from_go_mod(text: str) -> list[tuple[str, int]]:
    return [(m.group(1), _line_of(text, m.start(1)))
            for m in re.finditer(r"^\s+([a-z0-9.\-]+\.[a-z]{2,}/[^\s]+)\s+v", text, re.M)]


def _from_gradle(text: str) -> list[tuple[str, int]]:
    out = []
    for m in re.finditer(r"""(?:implementation|api|compile|runtimeOnly)\s*\(?\s*['"]([^'"]+)['"]""", text):
        coord = m.group(1)
        parts = coord.split(":")
        name = parts[1] if len(parts) >= 2 else coord
        out.append((name, _line_of(text, m.start(1))))
    return out


PARSERS = {
    "pom.xml": _from_pom, "package.json": _from_package_json, "pyproject.toml": _from_pyproject,
    "requirements.txt": _from_requirements, "go.mod": _from_go_mod,
    "build.gradle": _from_gradle, "build.gradle.kts": _from_gradle,
}


# --- deciding what to search for --------------------------------------------------


def search_tokens(name: str) -> list[str]:
    """Distinctive lowercase tokens that would appear in source if this dependency were used.

    Empty means "no way to check this one honestly".
    """
    base = name.split("/")[-1].lstrip("@").lower()
    base = re.sub(r"\.(js|ts)$", "", base)
    parts = [p for p in re.split(r"[^a-z0-9]+", base) if p]
    # Only real segments. Gluing the segments together ("springbootstarterweb") makes a token that
    # can never appear in any source file, which would read as damning and mean nothing.
    return sorted({p for p in parts if len(p) >= 4 and p not in GENERIC and not p.isdigit()})


def probe(repo: Path, sub: Submission) -> ProbeResult:
    manifests = [p for p in walk(repo) if p.name in MANIFESTS]
    if not manifests:
        return ProbeResult(summary="No dependency manifest found.", data={"declared": 0})

    declared: list[tuple[str, Path, int]] = []
    for path in manifests:
        parser = PARSERS.get(path.name)
        if not parser:
            continue
        for name, line in parser(read_text(path)):
            declared.append((name, path, line))

    # Read the tree once; every dependency is then a substring test against what is in memory.
    manifest_paths = {p.resolve() for p in manifests}
    code = {p: read_text(p).lower() for p in walk(repo, CODE_SUFFIXES) if p.resolve() not in manifest_paths}
    config = {p: read_text(p).lower() for p in walk(repo, CONFIG_SUFFIXES) if p.resolve() not in manifest_paths}

    def first_hit(files: dict[Path, str], tokens: list[str]) -> tuple[Path, int, str] | None:
        for path, text in files.items():
            for tok in tokens:
                for i, line in enumerate(text.splitlines(), start=1):
                    if tok in line:
                        return path, i, line.strip()
        return None

    used, config_only, unused, unchecked = [], [], [], []
    seen: set[str] = set()
    for name, path, line in declared:
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        tokens = search_tokens(name)
        if not tokens:
            unchecked.append((name, path, line, None))
            continue
        hit = first_hit(code, tokens)
        if hit:
            used.append((name, path, line, hit))
            continue
        hit = first_hit(config, tokens)
        (config_only if hit else unused).append((name, path, line, hit))

    evidence = []
    for name, path, line, hit in (config_only + unused)[:6]:
        text = read_text(path).splitlines()
        quote = text[line - 1].strip() if 0 < line <= len(text) else name
        evidence.append(line_evidence(repo, path, line, quote, sub, "deps"))
        if hit:  # where it *is* mentioned, so the evaluator can judge whether config counts
            hit_path, hit_line, hit_quote = hit
            evidence.append(line_evidence(repo, hit_path, hit_line, hit_quote, sub, "deps"))

    # Wording matters here. This probe matches *names*, so it cannot know that `jjwt-api` is
    # imported as `io.jsonwebtoken`. It reports what it actually did — looked for the name — and
    # leaves "is it really unused" to the human, which is the whole posture of the product.
    total = len(seen)
    parts = [f"Declared {total} dependenc{'y' if total == 1 else 'ies'}"]
    if config_only:
        names = ", ".join(n for n, *_ in config_only[:3]) + ("…" if len(config_only) > 3 else "")
        parts.append(f"{len(config_only)} named only in configuration, never in code ({names})")
    if unused:
        names = ", ".join(n for n, *_ in unused[:3]) + ("…" if len(unused) > 3 else "")
        parts.append(f"{len(unused)} whose name appears nowhere in the source ({names})")
    if not config_only and not unused:
        parts.append("every checkable name appears in the source")
    if unchecked:
        parts.append(f"{len(unchecked)} too generically named to check")
    summary = "; ".join(parts) + "."

    return ProbeResult(summary=summary, evidence=evidence, data={
        "manifests": [rel(repo, p) for p in manifests],
        "declared": total,
        "used": [n for n, *_ in used],
        "configOnly": [n for n, *_ in config_only],
        "unused": [n for n, *_ in unused],
        "unchecked": [n for n, *_ in unchecked],
    })
