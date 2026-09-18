# CLAUDE.md

**Read [`AGENTS.md`](AGENTS.md) first.** It is the canonical brief for this
repository — product thesis, the five invariants, layout, conventions, and the
documentation map. Everything in it applies to Claude Code.

## Claude Code specifics

**Before writing anything that calls the Claude API**, read
[`docs/04-model-orchestration.md`](docs/04-model-orchestration.md). It pins the
model tiering, the prompt-caching prefix layout, the structured-output contract,
and the citation mechanism. Getting these wrong is expensive rather than merely
incorrect.

**Model IDs used in this project** (do not substitute or append date suffixes):

- `claude-opus-5` — rubric compilation, contradiction detection, finding prose
- `claude-sonnet-5` — per-requirement evidence verification (the workhorse)
- `claude-haiku-4-5` — high-volume chunk labelling

**When touching types or schemas**, `docs/03-data-model.md` is the source of
truth and both language mirrors must be regenerated together.

**When adding a feature**, check it against the five invariants in `AGENTS.md`
before implementing. The most common violation is quietly reintroducing a score.

## Working agreements

- Prefer finishing the vertical slice over broadening any one layer.
- Deterministic probes before model calls — they are cheaper, faster, and more
  trusted by evaluators. If a check can be computed, compute it.
- Instrument `response.usage` on every model call from day one. Our published
  cost figure is currently an estimate and needs to become a measurement.
- Test fixtures live in `fixtures/` as small real repositories, including one
  with a planted prompt-injection payload. Do not delete that one — it is a
  security regression test and a demo asset.
