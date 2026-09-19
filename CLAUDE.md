# CLAUDE.md

**Read [`AGENTS.md`](AGENTS.md) first.** It is the canonical brief for this
repository — product thesis, the five invariants, layout, conventions, and the
documentation map. Everything in it applies to Claude Code.

## Claude Code specifics

**Before writing anything in `repoman/verify/`**, read
[`docs/04-model-orchestration.md`](docs/04-model-orchestration.md). It pins the
agent runtime (Strands Agents SDK), the two providers, the tool set and caps,
the structured-output contract, and the resolver rule.

**Environment variables** (the whole configuration surface):

| Var | Track 1 (local) | Track 2 (AWS) |
|---|---|---|
| `REPOMAN_OLLAMA_HOST` | `http://localhost:11434` | unset |
| `REPOMAN_MODEL_ID` | `qwen3:8b` | Bedrock inference profile ID for Claude Sonnet, copied from the console — never constructed from memory |
| `AWS_REGION` | unset | the region the model access / API key was made in, e.g. `eu-north-1` |
| `AWS_BEARER_TOKEN_BEDROCK` | unset | a Bedrock API key (console → API keys); boto3 ≥ 1.39 reads it directly. Short-term keys expire in 12 h. Never in a file. |
| `REPOMAN_BUCKET` | unset → `LocalStore(./data)` | bucket name → `S3Store` |
| `REPOMAN_TOKEN` | unset (no auth) | shared token for the web UI |
| `GROQ_API_KEY` | test bench only: Groq's OpenAI-compatible endpoint (`openai/gpt-oss-120b`) when neither Ollama nor Bedrock is at hand. Not a track; submissions leave the machine. | unset |

**When touching types**, `docs/03-data-model.md` is the source of truth and
`repoman/core/types.py` changes in the same commit.

**When adding a feature**, check it against the five invariants in `AGENTS.md`
before implementing. The most common violation is quietly reintroducing a score.
The second most common is adding a tool the agent can call that is not read-only.

## Working agreements

- Finish the vertical slice before broadening any layer. Day 1 has no UI.
- Deterministic probes before model calls — cheaper, faster, and more trusted by
  evaluators. If a check can be computed, compute it.
- Record `usage` from every agent run into `RunManifest.usage` from day one. The
  cost figure in `docs/04` is an estimate until it is a measurement.
- Smoke-test agent changes against Ollama first (free), then Bedrock.
- Test fixtures live in `fixtures/` as small real repositories, including one
  with a planted prompt-injection payload. Do not delete that one — it is a
  security regression test and a demo asset. Never install dependencies inside a
  fixture.
- Stdlib and already-installed packages before new dependencies. The dependency
  list is: `strands-agents`, `pydantic`, `fastapi`, `uvicorn`, `jinja2`,
  `pypdf`, `boto3`, `httpx`. Adding to it needs a sentence of justification in
  the commit message.
