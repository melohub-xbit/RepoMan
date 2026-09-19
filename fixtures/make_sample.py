"""Writes fixtures/sample_run/ — one realistic run of the Spring Boot example from docs/00.

Run: uv run python fixtures/make_sample.py
Replaced by a real engine run at the Day 1 checkpoint; until then it is the UI's data source.
All content is synthetic demonstration material.
"""

from __future__ import annotations

import json
from pathlib import Path

from repoman.core.types import (Artifact, Batch, Claim, Decision, DocSpan, Evidence, FileRange, Finding, GitObject,
                                HttpCapture, Level, Requirement, Rubric, Scale, RunManifest, RunStatus, SourceSpan,
                                Submission, UsageRecord)

OUT = Path(__file__).parent / "sample_run"
SHA = "9f3c2a1d7e4b8c0a5d6e7f8091a2b3c4d5e6f708"
REPO = "https://github.com/repoman-fixtures/taskflow-springboot"

RUBRIC_TEXT = """CS402 Final Project — Web Application Security
1. Authentication with JWT — 15 marks
2. Role-based authorization (admin/user) — 15 marks
3. Caching layer for read-heavy endpoints — 10 marks
4. Automated tests with meaningful coverage — 20 marks
5. Code quality and creativity — 40 marks
"""

batch = Batch(id="b_cs402", name="CS402 Final Projects · Fall 2026", eventWindow=("2026-08-24", "2026-09-14"))
rubric = Rubric(id="r_cs402", sourceText=RUBRIC_TEXT, compiledBy="fixture")
batch.rubricId = rubric.id


def req(rid, title, statement, weight, span, verifiable=True, reason=None, proposed="evaluator", scale=None):
    return Requirement(id=rid, rubricId=rubric.id, title=title, statement=statement, weight=weight,
                       sourceSpan=SourceSpan(startChar=span[0], endChar=span[1]), verifiable=verifiable,
                       unverifiableReason=reason, proposedBy=proposed, scale=scale or Scale())


TEST_LEVELS = Scale(kind="levels", levels=[
    Level(label="Missing", description="No automated tests.", points=0),
    Level(label="Token", description="A test file exists but exercises nothing meaningful.", points=5),
    Level(label="Partial", description="Core logic has tests; major paths untested.", points=10),
    Level(label="Solid", description="Core logic and error paths tested.", points=15),
    Level(label="Comprehensive", description="Tests cover logic, errors and integration; run in CI.", points=20),
])


rubric.requirements = [
    req("q1", "JWT authentication", "JWT tokens are issued on login and validated on every protected route.", 15, (49, 84)),
    req("q2", "Role-based authorization", "At least two roles exist and endpoints are restricted by role.", 15, (85, 133)),
    req("q3", "Caching layer", "A cache (Redis or similar) is configured and actually used by at least one read-heavy endpoint.", 10, (134, 185), scale=Scale(kind="check")),
    req("q4", "Automated tests", "Automated tests exist, run under a framework, and exercise core logic rather than only the application context.", 20, (186, 237), scale=TEST_LEVELS),
    req("q5a", "Code quality", "Consistent structure, no dead code, errors handled at boundaries.", 20, (238, 274), proposed="repoman"),
    req("q5b", "Creativity", "Creativity and originality of the solution.", 20, (238, 274), verifiable=False,
        reason="Not locatable in code, report or deployment; this criterion stays with the evaluator.", proposed="repoman"),
]

sub = Submission(id="s_taskflow", batchId=batch.id, source="github", repoUrl=REPO, commitSha=SHA)
sub.artifacts = [
    Artifact(id="a_repo", submissionId=sub.id, kind="repo", uri=REPO, sha256=SHA),
    Artifact(id="a_readme", submissionId=sub.id, kind="readme", uri=REPO + "/blob/main/README.md", sha256="c1" * 32),
    Artifact(id="a_report", submissionId=sub.id, kind="report", uri="report.pdf", sha256="d2" * 32),
    Artifact(id="a_deploy", submissionId=sub.id, kind="deploy", uri="https://taskflow-demo.example.app"),
]
batch.runIds = ["run_sample"]

report_pages = {
    "1": "TaskFlow — Final Project Report\nTeam 7 · CS402 · Fall 2026\n\nAbstract\nTaskFlow is a task management API with JWT authentication, full role-based access control, Redis caching and a comprehensive automated test suite.",
    "4": "3. Authentication\nUsers authenticate with email and password at POST /auth/login and receive a signed JWT. Every request to /api/** passes through JwtFilter, which validates the signature and expiry before the controller runs.",
    "9": "5. Authorization\nWe implemented full RBAC across all five user tiers: guest, member, lead, admin and owner. Each controller method is annotated with the roles permitted to call it, so an unprivileged caller cannot reach administrative operations.",
    "11": "6. Performance\nRead-heavy endpoints such as GET /api/tasks are served from Redis with a 60-second TTL, which reduced p95 latency by 70% in our load tests.",
    "14": "8. Testing\nWe wrote a comprehensive test suite covering authentication, authorization and task lifecycle, with over 40 test cases executed on every commit.",
}


def ev(locator, quote, prov="model", probe=None):
    return Evidence(submissionId=sub.id, locator=locator, quote=quote, provenance=prov, probeId=probe)


def fr(path, a, b):
    return FileRange(path=path, startLine=a, endLine=b, commitSha=SHA)


findings = [
    Finding(submissionId=sub.id, requirementId="q1", state="VERIFIED",
            summary="Tokens are issued by AuthController on login and validated by JwtFilter on every /api/** request; the deployed app returns 401 without a token.",
            evidence=[
                ev(fr("src/main/java/app/security/JwtFilter.java", 22, 60), 'String token = header.substring(7);\n        Claims claims = jwtService.parse(token);'),
                ev(fr("src/main/java/app/auth/AuthController.java", 34, 41), 'return ResponseEntity.ok(new TokenResponse(jwtService.issue(user)));'),
                ev(HttpCapture(url="https://taskflow-demo.example.app/api/tasks", status=401, capturedAt="2026-09-18T14:02:11Z", title="TaskFlow"),
                   "HTTP 401 Unauthorized without Authorization header", prov="probe", probe="deploy"),
                ev(DocSpan(artifactId="a_report", page=4), "Every request to /api/** passes through JwtFilter, which validates the signature and expiry"),
            ],
            confidence="high", confidenceReason="Three independent sources agree: filter code, controller code, live 401 from the deployment.",
            producedBy="fixture"),
    Finding(submissionId=sub.id, requirementId="q2", state="CONTRADICTED",
            summary="Two roles are defined (ADMIN, USER) and three of nine controllers carry an authorization annotation; the remaining six are reachable by any authenticated caller. The report claims full RBAC across five tiers.",
            evidence=[
                ev(fr("src/main/java/app/model/UserRole.java", 7, 12), "public enum UserRole {\n    ADMIN,\n    USER\n}"),
                ev(fr("src/main/java/app/security/SecurityConfig.java", 41, 68), '.requestMatchers("/admin/**").hasRole("ADMIN")'),
                ev(fr("src/main/java/app/api/OrderController.java", 29, 29), '@PreAuthorize("hasRole(\'ADMIN\')")'),
                ev(DocSpan(artifactId="a_report", page=9), "full RBAC across all five user tiers: guest, member, lead, admin and owner"),
            ],
            confidence="high", confidenceReason="Enum and config are unambiguous; the report's claim is directly quoted.",
            questions=["The report describes five user tiers — where are lead, member and owner defined?",
                       "Six controllers have no role restriction. Was that intentional for this submission?"],
            producedBy="fixture"),
    Finding(submissionId=sub.id, requirementId="q3", state="UNVERIFIED",
            summary="spring-boot-starter-data-redis is declared in pom.xml, but no source file imports RedisTemplate, uses @Cacheable, or configures a CacheManager. We searched the whole tree and found no usage.",
            evidence=[
                ev(fr("pom.xml", 48, 51), "<artifactId>spring-boot-starter-data-redis</artifactId>", prov="probe", probe="deps"),
                ev(fr("src/main/resources/application.yml", 1, 18), "spring:\n  datasource:\n    url: jdbc:postgresql://localhost/taskflow"),
                ev(DocSpan(artifactId="a_report", page=11), "served from Redis with a 60-second TTL"),
            ],
            confidence="medium", confidenceReason="Dependency declared with zero imports (deterministic). Absence in the tree is a search result, not a proof of absence.",
            searchExhausted=False,
            questions=["Where is the cache wired? Show a request that hits Redis.",
                       "The report cites a 70% p95 improvement — what produced that number?"],
            producedBy="fixture"),
    Finding(submissionId=sub.id, requirementId="q4", state="PARTIAL",
            summary="Fourteen JUnit 5 tests exist in three files. Eleven are context-load or smoke tests; none exercise JwtFilter, role checks, or task state transitions.",
            evidence=[
                ev(fr("src/test/java/app/TaskflowApplicationTests.java", 12, 16), "@Test\n    void contextLoads() {\n    }", prov="probe", probe="tests"),
                ev(fr("src/test/java/app/api/TaskControllerTest.java", 20, 38), "mockMvc.perform(get(\"/api/tasks\")).andExpect(status().isOk());"),
                ev(DocSpan(artifactId="a_report", page=14), "over 40 test cases executed on every commit"),
            ],
            confidence="high", confidenceReason="Test discovery is deterministic; the count and the report's figure disagree.",
            questions=["Which of the 14 tests would fail if JwtFilter stopped validating expiry?"],
            producedBy="fixture"),
    Finding(submissionId=sub.id, requirementId="q5a", state="PARTIAL",
            summary="Package structure is consistent (api / auth / model / security). Two controllers contain commented-out endpoints and OrderService swallows exceptions at line 71.",
            evidence=[
                ev(fr("src/main/java/app/service/OrderService.java", 66, 74), "} catch (Exception e) {\n            // TODO handle\n        }"),
                ev(fr("src/main/java/app/api/TaskController.java", 88, 104), "// @GetMapping(\"/export\")\n    // public ... exportTasks("),
            ],
            confidence="low", confidenceReason="Model-derived judgement on a criterion the evaluator proposed; treat as a pointer, not a verdict.",
            questions=["What is the intended behaviour when OrderService fails at line 71?"],
            producedBy="fixture"),
]

# flags are orthogonal — set on every finding so the banner can read them from any one
for f in findings:
    f.flagged = ["CONTRIBUTION_SKEW", "TIMELINE_ANOMALY"]

probes = {
    "deps": {"summary": "Declared 14 dependencies; 1 never imported (spring-boot-starter-data-redis).",
             "evidence": [findings[2].evidence[0].model_dump()]},
    "tests": {"summary": "JUnit 5 · 3 files · 14 tests · 11 context/smoke.",
              "evidence": [findings[3].evidence[0].model_dump()]},
    "git": {"summary": "47 commits · 3 authors · 82% of lines by one author · 61% of commits in the 6 h before the deadline.",
            "evidence": [ev(GitObject(commitSha="e41b7c2d9a0f", authorHash="7a1f3c9e2b44", committedAt="2026-09-14T21:47:03Z"),
                            "feat: add everything (+4,812 −120)", prov="probe", probe="git").model_dump()]},
    "injection": {"summary": "No instruction-shaped text found in README, comments or report.", "evidence": []},
    "deploy": {"summary": "https://taskflow-demo.example.app · 200 · \"TaskFlow\" · 2026-09-18T14:02:11Z",
               "evidence": [ev(HttpCapture(url="https://taskflow-demo.example.app", status=200, capturedAt="2026-09-18T14:02:11Z", title="TaskFlow"),
                               "<title>TaskFlow</title>", prov="probe", probe="deploy").model_dump()]},
}

manifest = RunManifest(id="run_sample", submissionId=sub.id, rubricId=rubric.id, rubricVersion=1, commitSha=SHA,
                       modelId="fixture", finishedAt="2026-09-18T14:05:40Z",
                       usage=[UsageRecord(**{"pass": "verify", "inputTokens": 412_000, "outputTokens": 9_300, "cacheReadTokens": 0})])

# what the submission says about itself — the README's one sentence makes four claims; two hold up
README_LINE = "Task management API with JWT authentication, full role-based access control, Redis caching and a comprehensive test suite."
claims = [
    Claim(id="c1", submissionId=sub.id, statement="JWT authentication protects the API.", source=ev(fr("README.md", 3, 3), README_LINE)),
    Claim(id="c2", submissionId=sub.id, statement="Redis is used as a cache.", source=ev(fr("README.md", 3, 3), README_LINE)),
    Claim(id="c3", submissionId=sub.id, statement="The test suite is comprehensive.", source=ev(fr("README.md", 3, 3), README_LINE)),
]
findings += [
    Finding(submissionId=sub.id, requirementId="c1", subject="claim", state="VERIFIED",
            summary="JwtFilter validates a bearer token on every /api/** request.",
            evidence=[ev(fr("src/main/java/app/security/JwtFilter.java", 22, 60), 'String token = header.substring(7);\n        Claims claims = jwtService.parse(token);')],
            confidence="high", confidenceReason="Read the filter directly.", producedBy="fixture"),
    Finding(submissionId=sub.id, requirementId="c2", subject="claim", state="UNVERIFIED",
            summary="The Redis starter is declared but nothing imports it, no @Cacheable, no CacheManager.",
            evidence=[ev(fr("pom.xml", 48, 51), "<artifactId>spring-boot-starter-data-redis</artifactId>", prov="probe", probe="deps")],
            confidence="medium", confidenceReason="Declared with zero imports; absence in the tree is a search result.",
            questions=["Show one request that reads from Redis."], producedBy="fixture"),
    Finding(submissionId=sub.id, requirementId="c3", subject="claim", state="PARTIAL",
            summary="Fourteen tests, eleven of them context-load or smoke checks.",
            evidence=[ev(fr("src/test/java/app/TaskflowApplicationTests.java", 12, 16), "@Test\n    void contextLoads() {\n    }", prov="probe", probe="tests")],
            confidence="high", confidenceReason="Test discovery is deterministic.", producedBy="fixture"),
]

OUT.mkdir(parents=True, exist_ok=True)


def dump(name, obj):
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(by_alias=True)
    elif isinstance(obj, list):
        obj = [o.model_dump(by_alias=True) if hasattr(o, "model_dump") else o for o in obj]
    (OUT / name).write_text(json.dumps(obj, indent=2, sort_keys=True))


dump("batch.json", batch)
dump("rubric.json", rubric)
dump("submission.json", sub)
dump("findings.json", findings)
dump("claims.json", claims)
dump("probes.json", probes)
dump("manifest.json", manifest)
dump("report_pages.json", report_pages)
dump("status.json", RunStatus(stage="done", detail="6 requirements", done=6, total=6))
dump("decisions.json", [])

# --- a tiny checkout so the in-place code view has real lines at the cited numbers ---
def java_file(path: str, placed: dict[int, str], total: int = 110, pkg: str = "app"):
    lines = [f"package {pkg};", "", "import org.springframework.stereotype.Component;", ""]
    body = {}
    for start, text in placed.items():
        for i, t in enumerate(text.splitlines()):
            body[start + i] = t
    out = []
    for n in range(1, total + 1):
        if n <= len(lines):
            out.append(lines[n - 1])
        elif n in body:
            out.append(body[n])
        elif n % 9 == 0:
            out.append("")
        else:
            out.append(f"        // line {n}")
    f = OUT / "repo" / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(out) + "\n")


java_file("src/main/java/app/security/JwtFilter.java", {
    18: "public class JwtFilter extends OncePerRequestFilter {",
    22: "    @Override\n    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain) {\n        String header = req.getHeader(\"Authorization\");\n        if (header == null || !header.startsWith(\"Bearer \")) {\n            res.setStatus(401);\n            return;\n        }\n        String token = header.substring(7);\n        Claims claims = jwtService.parse(token);\n        if (claims.getExpiration().before(new Date())) {\n            res.setStatus(401);\n            return;\n        }",
    60: "    }", 61: "}"}, total=62)
java_file("src/main/java/app/auth/AuthController.java", {
    30: "    @PostMapping(\"/auth/login\")\n    public ResponseEntity<TokenResponse> login(@RequestBody LoginRequest body) {\n        User user = users.authenticate(body.email(), body.password());\n        if (user == null) return ResponseEntity.status(401).build();\n        return ResponseEntity.ok(new TokenResponse(jwtService.issue(user)));\n    }"}, total=48)
java_file("src/main/java/app/model/UserRole.java", {7: "public enum UserRole {\n    ADMIN,\n    USER\n}"}, total=12)
java_file("src/main/java/app/security/SecurityConfig.java", {
    41: "    @Bean\n    SecurityFilterChain filterChain(HttpSecurity http) throws Exception {\n        http\n            .csrf(csrf -> csrf.disable())\n            .authorizeHttpRequests(auth -> auth\n                .requestMatchers(\"/auth/**\").permitAll()\n                .requestMatchers(\"/admin/**\").hasRole(\"ADMIN\")\n                .requestMatchers(\"/api/**\").authenticated()\n                .anyRequest().permitAll())\n            .addFilterBefore(jwtFilter, UsernamePasswordAuthenticationFilter.class);\n        return http.build();\n    }"}, total=70)
java_file("src/main/java/app/api/OrderController.java", {29: "    @PreAuthorize(\"hasRole('ADMIN')\")\n    @DeleteMapping(\"/orders/{id}\")"}, total=60)
java_file("src/main/java/app/api/TaskController.java", {
    88: "    // @GetMapping(\"/export\")\n    // public ResponseEntity<byte[]> exportTasks(\n    //         @RequestParam String format) {\n    //     return ResponseEntity.ok(exporter.export(format));\n    // }"}, total=110)
java_file("src/main/java/app/service/OrderService.java", {
    66: "        try {\n            payments.charge(order);\n        } catch (Exception e) {\n            // TODO handle\n        }"}, total=90)
java_file("src/test/java/app/TaskflowApplicationTests.java", {
    10: "@SpringBootTest\nclass TaskflowApplicationTests {\n    @Test\n    void contextLoads() {\n    }\n}"}, total=16)
java_file("src/test/java/app/api/TaskControllerTest.java", {
    20: "    @Test\n    void listsTasks() throws Exception {\n        mockMvc.perform(get(\"/api/tasks\")).andExpect(status().isOk());\n    }"}, total=40)
pom = ["<project>", "  <modelVersion>4.0.0</modelVersion>", "  <artifactId>taskflow</artifactId>", "  <dependencies>"]
pom += [f"    <!-- dep {i} -->" for i in range(5, 47)]
pom += ["    <dependency>", "      <groupId>org.springframework.boot</groupId>", "      <artifactId>spring-boot-starter-data-redis</artifactId>", "    </dependency>", "  </dependencies>", "</project>"]
(OUT / "repo" / "pom.xml").write_text("\n".join(pom) + "\n")
(OUT / "repo" / "src/main/resources").mkdir(parents=True, exist_ok=True)
(OUT / "repo" / "src/main/resources/application.yml").write_text("spring:\n  datasource:\n    url: jdbc:postgresql://localhost/taskflow\n    username: taskflow\n  jpa:\n    hibernate:\n      ddl-auto: update\nserver:\n  port: 8080\n")
(OUT / "repo" / "README.md").write_text("# TaskFlow\n\nTask management API with JWT authentication, full role-based access control, Redis caching and a comprehensive test suite.\n")

print(f"wrote {OUT}")
