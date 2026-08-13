# Decisions

Contentious calls — ones that change architecture, add a dependency, touch the
CLI contract, or move API cost. Each records the problem, the plan I followed
(A), the alternative (B), and a recommendation. **Work continued on option A**;
say the word and any of these flips.

---

## D1 — The name `mutagen` is taken on PyPI

**Problem.** `pypi.org/project/mutagen` is the audio-metadata library, one of
the most-installed packages in the ecosystem. We cannot have it. Checked
2026-08-13: `mutagen-cli` → 404 (free), `mutant-llm` → 404 (free),
`llm-mutation` → taken.

**A (current).** Distribution name `mutagen-cli`, import package `mutagen_cli`,
console script `mutagen`. Users type `pip install mutagen-cli` then `mutagen run`.

**B.** Rename the project outright — `mutant`, `greenlie`, `nullhypothesis`,
`does-it-catch`. Keeps one name everywhere.

**Status 2026-08-13.** Published: `mutagen-cli` 0.1.0 is live on PyPI —
<https://pypi.org/project/mutagen-cli/0.1.0/>. `pip install mutagen-cli` then
`mutagen run` is now the documented quickstart everywhere (README, `action.yml`).

**Recommendation: A, with a caveat.** The split name is normal (`ripgrep`/`rg`)
and the CLI verb is what people actually type. The caveat is real though: if a
user has the audio library installed, `import mutagen` in their code and our
`mutagen` binary coexist fine, but the two names in one requirements file will
confuse people. If you want zero ambiguity, pick a fresh name now — renaming
later is much worse.

## D2 — Default model is `claude-opus-5`

**Problem.** Model choice is the entire cost profile. One call per changed
function, plus one per survivor with `--invent`.

**A (current).** `claude-opus-5` ($5/$25 per Mtok) at `effort=medium`,
overridable with `--model` / `--effort`.

**B.** Default to `claude-sonnet-5` ($3/$15) and let people opt up.

**Recommendation: A.** Mutant quality *is* the product — a cheap model that
emits equivalent mutants produces a noisy report, which is the failure mode we
are specifically trying to avoid. The disk cache makes iteration free, and
`--dry-run` shows the call count before spending anything. But this is your
money: if "costs pennies" matters more than mutant quality, switch the default
to Sonnet and quote both in BENCHMARKS.md.

**Update 2026-08-13.** Superseded by D8: the default *provider* is now
OpenRouter, and its default model is `anthropic/claude-sonnet-5` ($2/$10).
`--provider anthropic` still defaults to `claude-opus-5` as decided here.

## D3 — No seed; reproducibility comes from the cache

**Problem.** The plan asks for a fixed seed so benchmark numbers are
reproducible.

**A (current).** There is no seed. `temperature`, `top_p`, and `top_k` are
**rejected with a 400** by Claude Opus 5 / Sonnet 5 / Opus 4.7+ — the knob no
longer exists. Reproducibility is provided by the on-disk cache: keep
`.mutagen/cache/` and a run replays exactly. `scripts/benchmark.py` leans on
this to run fully offline.

**B.** Pin an older model that still accepts `temperature=0`.

**Recommendation: A.** Pinning a deprecated model to get a knob that never
guaranteed identical output anyway is a bad trade. Quote live benchmarks as a
range over N runs, or ship the cache directory alongside the numbers.

## D4 — Structured outputs instead of parsing JSON out of prose

**Problem.** The plan says "return strict JSON". The usual implementation is
"ask nicely, strip markdown fences, retry on parse failure".

**A (current).** `output_config.format` with a JSON schema. The API constrains
generation, so the response is valid JSON matching the schema by construction.
No fence-stripping, no retry loop, no repair prompt.

**B.** Free-text JSON plus a parser with retries. Works on any provider.

**Recommendation: A.** It deletes an entire class of failure. The cost is that
`provider.py` now assumes a provider with schema-constrained output; an Ollama
backend would need the free-text path added back as a fallback. That is a
20-line addition when someone actually wants it, not a reason to build both now.

## D5 — Sandboxing: full repo copy per worker

**Problem.** Mutants must run in isolation, in parallel, without ever touching
the user's working tree.

**A (current).** `shutil.copytree` of the repo (minus `.git`, `.venv`,
`node_modules`, caches) once per worker. Each worker mutates and restores one
file at a time inside its own copy.

**B.** `git worktree add` per worker — cheaper, but only materialises *committed*
state, so uncommitted edits (the main use case) would not be mutated. Or
hardlink the tree, which is near-instant but means a test that writes to a repo
file corrupts the original.

**Recommendation: A.** B's worktree variant is wrong for the primary workflow
and the hardlink variant trades a rare-but-catastrophic failure for startup
speed. If copy time becomes the bottleneck on a large repo, the right fix is
`copytree(..., copy_function=os.link)` behind an opt-in flag, not a default.

## D6 — One LLM call per function, no batching

**Problem.** The plan allows grouping functions into one call to save tokens.

**A (current).** Exactly one call per function.

**B.** Pack N functions per call, batched by token budget.

**Recommendation: A.** Two reasons beyond simplicity. The prompt carries *that
function's* tests, which is what makes the mutants aimed rather than generic —
batching dilutes it. And the cache key is per function, so editing one function
re-bills one call instead of the whole batch. Batching would cut the first-run
bill on a large diff; revisit if that bill turns out to be the complaint.

## D7 — `--invent` always self-verifies, in both directions

**Problem.** The plan scopes verification to `--invent-apply`.

**A (current).** Every suggested test is run twice before it is shown: it must
pass on the real code *and* fail on the mutant. Results are labelled
`verified` / `rejected` (fails on your code) / `weak` (passes, but doesn't catch
the bug). `--invent-apply` writes only `verified` ones to disk.

**B.** Verify only when writing files, as specified.

**Recommendation: A.** An unverified suggested test is a guess presented as a
fix. The sandbox is already warm, so the check costs two short pytest runs and
no tokens. This is the difference between the feature being useful and the
feature lying.

## D8 — OpenRouter is the default provider

**Problem.** A large share of the target audience (Russia/CIS) has no direct
access to the Anthropic API but can reach OpenRouter.

**A (current).** `--provider {openrouter,anthropic}`, default `openrouter`,
default model `anthropic/claude-sonnet-5` ($2/$10 per Mtok as of 2026-08-13 —
the price/quality optimum for mutant generation). Keys resolve from
`OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` first, then `.mutagen/config.json`
(`openrouter_api_key` / `anthropic_api_key`; the old bare `api_key` still means
Anthropic). The provider name is part of the LLM cache key, so identical
prompts to two providers never share an entry.

**B.** Keep Anthropic the default and document OpenRouter as the workaround.

**Recommendation: A.** The default should work for the majority of the
audience; Anthropic users pass one flag. Two OpenRouter-specific traps shaped
the implementation (checked 2026-08-13, see
<https://openrouter.ai/docs/use-cases/reasoning-tokens> and the migration
guides under <https://openrouter.ai/docs/guides>):

- `anthropic/claude-sonnet-5` and `anthropic/claude-opus-5` run with **reasoning
  enabled by default** there. Reasoning blocks pollute the JSON reply and
  inflate cost/latency, so every request sends `reasoning: {"enabled": false}`.
  Config key `openrouter_reasoning: true` opts back in.
- `temperature` / `top_p` / `top_k` are **silently ignored** by those models on
  OpenRouter, so the provider never sends them (the Anthropic provider can't
  send them either — the API rejects them, see D3).

## D9 — OpenRouter via plain httpx, not the openai SDK

**Problem.** OpenRouter is OpenAI-compatible; the brief suggested the official
`openai` SDK with a custom `base_url`, or raw `httpx` if the SDK is too heavy.

**A (current).** One `httpx.Client` POST to `/chat/completions`. `httpx` is
already a transitive dependency (via `anthropic`), so it becomes a direct one
and adds nothing to the install. The request is ~40 lines: messages,
`response_format` with the JSON schema (retried once without it on a 400 — not
every routed model supports structured outputs), `reasoning` toggle, usage
mapping. Tolerant JSON extraction (`extract_json`) handles prose and fences
when schema mode is unavailable.

**B.** Depend on `openai` and use `OpenAI(base_url=...)`.

**Recommendation: A.** The SDK would add a large dependency tree to buy us a
client constructor and typed errors we don't otherwise use. If a third
OpenAI-compatible provider appears and the payload logic grows, revisit.

## D10 — A mutation may only land inside its own function

**Problem.** The model is shown one function and returns a SEARCH/REPLACE
block. Matching ran over the whole file, so a block that also occurs elsewhere
— `if n < 1:`, `return []`, `self._data.move_to_end(key)` — could be applied to
a different function. The mutant then runs the *target's* mapped tests against
an untouched target: a survivor that proves nothing, indistinguishable in the
report from a real one. The fuzzy tier made it worse, since a 0.85 match can be
found almost anywhere in a file of similar code.

**A (current).** `apply_search_replace(..., span=(start_line, end_line))`.
Every tier — exact, whitespace-normalised, reindented, fuzzy — only considers
windows fully inside the target's line range. No match inside the span is
`unapplicable`, with a reason that says so.

**B.** Keep whole-file matching but reject when a block matches more than once.

**Recommendation: A.** B still allows the single-match-in-the-wrong-function
case, which is the one that actually lies. The span is free — `Target` already
carries it, it is exactly the text the model was shown, and the file in the
worker copy is pristine before each mutant, so the line numbers cannot drift.
The measured cost is zero: Runs A and B in BENCHMARKS.md reproduce
byte-identically, because well-behaved blocks were always in range.

**Residual risk.** This closes misplacement *within* a file. It does not close
the other artefact route — a mutant run against test files the mapping picked
wrongly. **Closed by D11**, which replaces the heuristic with coverage data.

## D11 — Test mapping is measured, not guessed

**Problem.** D10 closed misplacement *within* a file and named the remaining
artefact route: the heuristic mapping (filename similarity + symbol references,
top three files) can pick tests that cannot kill the mutant while missing the
one that can. The mutant then survives for a reason that has nothing to do with
the test suite's quality. This was the largest known source of false survivors.

**A (current).** The baseline run — which happens anyway, to prove the suite is
green — runs under `pytest-cov` with `--cov-context=test`. That yields, per
line, the node ids of the tests that executed it. Each mutant is then run
against the tests covering *the lines that mutant changed*
(`ApplyResult.start_line..end_line`, not the whole function). The map is built
once and reused: the code under test is identical for every mutant, and each
mutant is applied to a pristine copy.

**B.** Keep the heuristic and widen it (more files, more scoring signals).

**C.** Import-graph analysis instead of runtime coverage.

**Recommendation: A.** B tunes a guess; there is no scoring function that
turns "this file does not mention the function" into "this file exercises it
through two layers of indirection". C misses everything dynamic —
fixtures, parametrisation, dependency injection — which is most of a real
suite. Runtime coverage is the only thing that answers the actual question.

**Consequences accepted:**

- **The baseline now runs the whole suite**, where before it ran only the
  heuristically selected subset. A map built from a subset could only confirm
  the guess that produced it. Measured cost on the polygon: 0.25 s → 0.35 s.
  Per-mutant runs get *faster* in exchange, since the selection is node ids.
- **Generation moved after the baseline.** It had to, because the prompt
  carries the covering tests. A welcome side effect: a red suite is now found
  before any money is spent, not after.
- **`pytest-cov` is optional, and its absence is stated.** No map means the
  heuristic, and the report says `mapping: heuristic (install pytest-cov for
  precise coverage mapping)` in the terminal, the markdown and the JSON. A
  silent downgrade would be worse than the old behaviour.
- **The cache key moved**, because the prompt now shows different tests.
  `scripts/benchmark.py` builds the map the same way through
  `runner.collect_coverage_map`, or its seeded "offline" run would miss.
- **`--dry-run` stays on the heuristic** and says so: it must not run the
  suite.

**New verdict shading: `unreached`.** When the map says nothing executes the
mutated lines, the mutant is a survivor that we do not run — no test outcome
can depend on code no test reaches. It stays a `survived` verdict (the score
keeps exactly the shape it had) but is reported in its own section, because
"your assertion is too weak" and "nothing runs this at all" are different
problems with different fixes.

**Residual risk.** A file absent from the map is left on the heuristic rather
than called uncovered: "never imported" and "excluded by a source filter" look
identical from here, and guessing wrong in that direction would invent blind
spots that do not exist.

---

## Post-MVP ideas

Parked deliberately. Not in scope.

- Other languages and runners (JS/Vitest, Go, Rust) — needs a per-language AST
  and runner adapter; nothing in the current design is generic and that is fine.
- A plugin system for providers. Add OpenAI/Ollama as concrete classes in
  `provider.py` first; extract an interface only if a third one appears.
- ML-based ranking of survivors by "likelihood of being a real gap".
- Web UI / hosted dashboard.
- Incremental mode: remember verdicts per (function hash, mutant hash) across
  runs and only re-run what changed.
- Coverage-guided targeting: skip functions with zero line coverage instead of
  paying for mutants that no test could ever kill.
- Mutating tests themselves (does removing an assertion change anything?) as a
  cheaper first-pass heuristic.
- Multi-mutant runs (higher-order mutation) to find tests that only catch bugs
  in isolation.
