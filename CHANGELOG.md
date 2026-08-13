# Changelog

Entries tagged `[improvement]` were not in the plan — they are changes made on
my own initiative because they cut a step, cut noise, cut time, or fixed an edge
case that would otherwise have produced a wrong number.

## 0.1.0 — unreleased

### The tool

- **OpenRouter is now the default LLM provider** (`--provider openrouter`),
  alongside Anthropic (`--provider anthropic`). Motivation: much of the
  audience (Russia/CIS) cannot reach the Anthropic API directly; OpenRouter
  works there without a VPN. Default model on OpenRouter is
  `anthropic/claude-sonnet-5`; `--model` overrides. Keys come from
  `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY`, then `.mutagen/config.json`
  (`openrouter_api_key` / `anthropic_api_key`; the legacy bare `api_key` still
  means Anthropic). See DECISIONS.md D8.
- OpenRouter transport is plain `httpx` (already a transitive dependency) —
  no `openai` SDK. Structured JSON output is requested via `response_format`
  with a retry without it on 400, plus tolerant JSON extraction as a fallback.
  Reasoning is explicitly disabled per request (`reasoning.enabled=false`) —
  it is on by default for the Claude 5 models on OpenRouter and breaks JSON
  parsing; `{"openrouter_reasoning": true}` in the config opts back in.
  Sampling parameters are never sent (silently ignored by those models).
  See DECISIONS.md D9.
- LLM cache keys now include the provider name, so the same prompt to two
  providers never shares an entry. (Old entries simply miss.)
- Cost reporting uses the provider's own usage fields against the built-in
  price table; `{"prices": {"model/id": [in, out]}}` in the config overrides
  it. A model with no known price is reported as **"cost unavailable"** instead
  of a misleading $0. The JSON report gains `usage.unpriced_calls`.
- The GitHub Action takes a `provider` input (still defaults to `anthropic`
  there, so existing workflows don't break) and an `openrouter-api-key` input;
  its `model` input now defaults to the provider's default.
- `mutagen run` — diffs the working tree against the merge base with `main`,
  maps the changed lines to whole functions via `ast`, and mutates only those.
- `--all`, `--path`, `--base` for the other scopes; `--max-mutants`,
  `--max-files` as ceilings.
- LLM-generated mutants: one call per function, prompted with that function's
  own tests so the mutations aim at the blind spots. Returned as
  SEARCH/REPLACE blocks.
- Fuzzy application: exact → whitespace-normalised → re-indented → `difflib`
  above a 0.85 threshold. Anything below is `unapplicable` and excluded from the
  score.
- Parallel execution across per-worker copies of the repo, with per-mutant
  timeouts. Verdicts: `killed`, `survived`, `timeout`, `error`, `unapplicable`.
- Terminal report via `rich` built around the survivors; `--report-md` and
  `--report-json` exports.
- `--invent` writes the missing test for each survivor; `--invent-apply` saves
  verified ones to `tests/mutagen_generated/`.
- On-disk LLM cache in `.mutagen/cache/`, keyed per function.

### Correctness fixes found while building

- **`[improvement]` Bytecode caching could hide a mutant entirely.** A mutation
  that does not change a file's byte length (`min`→`max`, `<`→`<=`) written
  within the same second as a previous run reuses the stale `.pyc`, because
  CPython invalidates on mtime+size. The mutant never ran and was reported as a
  survivor. Test subprocesses now run with `PYTHONDONTWRITEBYTECODE=1`. There is
  a regression test for exactly this.
- **`[improvement]` Exact matching is line-aligned.** A raw substring search
  would match `x = 1` inside `max_x = 10` and corrupt the file. Matching now
  happens on whole lines.
- **`[improvement]` An editable install of the target project made every
  mutant a false survivor.** A src-layout project installed with
  `pip install -e .` puts a `.pth` in site-packages pointing at the **original**
  tree. Tests running inside the worker copy therefore imported the unmutated
  package, every mutant "survived", and the reports looked plausible — the
  worst possible failure mode for this tool. Fixed by putting the worker copy
  (`<workdir>/src` then `<workdir>`) at the front of `PYTHONPATH` for every
  pytest subprocess, so the mutated package shadows the editable install.
  Regression test: `test_editable_src_install_cannot_shadow_the_worker_copy`.
- **`[improvement]` Mutants that do not parse are `unapplicable`, not `killed`.**
  A syntax error makes every test error out, which naïvely reads as a kill and
  inflates the score. The result is `ast.parse`d before it counts.
- **`[improvement]` Baseline check before mutating.** If the suite is already
  red, every mutant looks killed. mutagen runs the selected tests unmutated
  first and refuses to continue, printing the failures.
- **`[improvement]` No-op mutations are dropped** at generation *and* at apply
  time.
- **`[improvement]` pytest exit codes 2–5 map to `error`, not `killed`.**
  Collection errors and "no tests collected" are not evidence of a good suite.

### Speed and cost

- **`[improvement]` Relevant tests only.** Functions are mapped to test files by
  filename and symbol reference; a mutant runs just those. No mapping falls back
  to the full suite rather than skipping.
- **`[improvement]` `-x` on mutant runs.** We only need to know *whether*
  something failed, so stop at the first failure.
- **`[improvement]` Adaptive timeout.** The baseline run is timed and the
  per-mutant budget is raised to `3× baseline + 5s` when that exceeds
  `--timeout`, so a slow suite does not report false timeouts.
- **`[improvement]` Structured outputs** (`output_config.format` + JSON schema)
  instead of parsing JSON out of prose — removes the fence-stripping and
  retry-on-malformed-JSON path entirely. See DECISIONS.md D4.

### UX

- **`[improvement]` `--dry-run`** prints the plan, the call count, and the
  function→test mapping, and spends nothing. Makes the cost estimate actionable
  instead of just informative.
- **`[improvement]` `--fail-under`** exits non-zero below a score threshold, so
  the tool is a CI gate without a wrapper script.
- **`[improvement]` Untracked files are in scope.** `git diff` never shows a
  brand-new file; a tool aimed at "code you just wrote" that ignores new files
  would be missing the main case.
- **`[improvement]` The project's own virtualenv is detected** (`.venv`, `venv`,
  `env`) so tests run against the right interpreter when mutagen is installed
  globally. `--python` overrides.
- **`[improvement]` API key falls back to a config file**
  (`.mutagen/config.json`, then `~/.config/mutagen/config.json`) so it need not
  live in the shell environment.
- **`[improvement]` The unapplicable rate is surfaced** in the report when it
  exceeds 15% — it is the signal that the prompt needs work, so the tool says so
  instead of hiding it in a JSON field.

### Honesty

- **`[improvement]` `--invent` verifies in both directions, always** — the
  suggested test must pass on the real code and fail on the mutant, and is
  labelled `verified` / `rejected` / `weak` accordingly. The plan scoped
  verification to `--invent-apply`; an unverified suggestion is a guess dressed
  as a fix. See DECISIONS.md D7.
- Mutation score is `killed / (killed + survived)`. Timeouts, errors, and
  unapplicable mutants are excluded from both numerator and denominator.

### Project

- Polygon under `tests/fixtures/victim_project/`: 15 functions across 5 modules,
  36 passing tests, 18 of them deliberately worthless, with the blind spots they
  leave documented in `WEAK_TESTS.md`.
- 74 tests for mutagen itself. They run offline and for free — the pipeline
  tests use a replay provider and real pytest subprocesses, and the CLI tests
  pre-seed the disk cache so no API key is needed. `ruff` config in
  `pyproject.toml`; `ruff check .` is clean.
- MIT `LICENSE` file; `pyproject` declares it with the PEP 639 `license` /
  `license-files` fields.
- **`[improvement]` `scripts/benchmark.py`** runs the polygon end-to-end and
  compares every verdict against a hand-written golden standard. Offline by
  default (28 canned mutants, deterministic, zero cost); `--live` for the real
  thing. Flags: `--max-mutants` (cheap live smoke tests), `--replies`, and
  `--save-report`.
- **`[improvement]` `scripts/dump_prompts.py`** writes out the exact
  per-function prompts mutagen would send, so a model that is not reachable over
  HTTP can answer them offline and have its mutants fed back through the real
  pipeline via `benchmark.py --replies`. This is what made Phase 4's mutant
  quality measurable without an API key.

### Pre-publication audit (2026-08-13)

An adversarial pass over the whole thing before it goes public. Findings and
their fixes:

- **A SEARCH/REPLACE block could be applied to the wrong function.** Matching
  ran over the whole file, so a block that also occurs in a neighbouring
  function was applied *there* (exact matching even reported
  `"exact (2 matches, used first)"` and carried on). mutagen then ran the
  *target's* tests against an untouched target and reported a survivor that was
  pure artefact — or, symmetrically, a kill earned by a function nobody asked
  about. Matching is now confined to the target's own `start_line..end_line`,
  for every strategy including the fuzzy one; a block that matches nowhere
  inside the function is `unapplicable`. Runs A and B in BENCHMARKS.md
  reproduce byte-identically after the change. Regression tests in
  `tests/test_apply.py` and `test_mutation_lands_in_the_target_function_not_a_twin`.
- **Failed LLM calls vanished from the cost report.** A request that came back
  unusable was skipped without being counted, so the footer under-reported both
  the call count and the money spent (seen live: 4 requests issued, 3 reported).
  `Usage.failed_calls` now tracks them and the report says the provider may
  still have billed them. JSON reports gain `usage.failed_calls`.
- **`--all` overstated the bill by ~10x.** The pre-run summary quoted one LLM
  call per function, but generation stops at `--max-mutants`: 90 functions with
  the default budget is 9 calls, not 90. `estimate()` now bounds calls by the
  mutant budget.
- **No API key produced one warning per function and then the wrong
  diagnosis** ("the model returned no usable mutants. Try --max-mutants or a
  different --model."). Repeated warnings are collapsed by reason, and a run
  that produced nothing with no key resolved reports the missing key instead.
- **`--path` outside the repo, and `--python` pointing at a nonexistent
  interpreter, both raised raw tracebacks.** Now plain error messages.
- **A missing pytest was reported as "your test suite is not green".**
  `python -m pytest` without pytest exits 1, same as a failing suite; the
  message now names the real cause and the fix.
- **The OpenRouter cache key ignored the reasoning toggle**, so flipping
  `openrouter_reasoning` replayed the other mode's answers. It is now part of
  the key (the Anthropic side already keyed on `effort`).
- **`scripts/benchmark.py` went live whenever an API key happened to be
  exported** — including the key the README tells you to export — so the
  documented "offline, zero API calls" command could quietly bill you. `--live`
  is now required, and errors if the key is absent.
- Repo hygiene: the project had no git repository of its own (it sat inside an
  unrelated parent repo with zero commits). Added `LICENSE`, `.gitignore` rules
  for `reports/`, `mutagen-cache/` and `.env`, and removed unused imports.

### Phase 4 validation

- Engine validation (Run A): 28 canned mutants, **28/28 verdicts matched** the
  hand-written golden standard.
- Mutant quality (Run B): 43 model-written mutants across 15 functions —
  8 killed, 35 survived, **0% unapplicable** (threshold 15%), **5.7% junk**
  (threshold 20%), **18/22 documented blind spots covered**, plus **7 blind
  spots the polygon's own `WEAK_TESTS.md` had missed**. Both thresholds passed
  on the first attempt, so **no prompt iteration was needed**.
- Live API (Run D, OpenRouter): measured 2026-08-13 on both default-relevant
  models — `anthropic/claude-sonnet-5`: 40 mutants, **0% unapplicable**,
  **12.9% junk**, 11/22 documented blind spots, **$0.26** per full `--invent`
  pass; `anthropic/claude-opus-5`: **0% unapplicable**, **3.0% junk**, 14/22
  blind spots, **$0.68**. Both passed the 15%/20% thresholds with **zero
  prompt iterations**. Run C (direct Anthropic API) remains unmeasured — no
  credentials.
- Audit re-verification (Run E, 2026-08-13): Runs A and B reproduced from a
  clean copy with a fresh venv, plus two live OpenRouter runs ($0.014 and
  $0.016) exercising the HTTP, structured-output, cost and `--invent` paths.
- Known gap found by the measurement: the model clusters several mutants on one
  line (3 on `__len__`'s single `return`), reporting one blind spot repeatedly.
  Deduplicating survivors by mutated line would tighten the report.
