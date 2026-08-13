# Changelog

Entries tagged `[improvement]` were not in the plan — they are changes made on
my own initiative because they cut a step, cut noise, cut time, or fixed an edge
case that would otherwise have produced a wrong number.

## 0.1.3 — 2026-08-14

- **Fixed:** offline benchmark broken — `seed_cache()` key drifted from the
  provider cache key (missing `max_tokens`, schema not serialized); the
  benchmark now runs in CI to prevent drift.
- **Fixed:** the GitHub Action gate no longer masks a mutagen crash (exit 1
  without a valid report) as a green check.
- **Fixed:** `mutagen --version` reported a stale hardcoded version; it is now
  read from package metadata.
- **Fixed:** the GitHub Action failed or silently misbehaved on shallow
  clones — merge-base check now has an unshallow fallback.
- **Added:** retry with exponential backoff (cap 30s, respects `Retry-After`)
  on 429/5xx/network errors for OpenRouter.
- **Added:** automatic default-branch detection (`origin/HEAD` → `main`/
  `master`); `--base` documented for other cases.
- **Added:** a warning when functions are skipped due to `--max-mutants`.
- **Added:** dedup of identical SEARCH/REPLACE mutants within one LLM
  response.
- **`[security]`** cache files are now written with `0600` permissions;
  the system prompt is hardened against instructions embedded in source
  code.
- **Docs:** comparison table with mutmut / cosmic-ray / Mutahunter; the
  `--invent` claim softened; `error` verdict semantics documented; a
  prices-as-of date added to markdown reports; README.en.md brought to
  parity with README.md.

## 0.1.2 — 2026-08-14

- **`[improvement]` Benchmarked against two independent third-party
  repositories, not just the author's own projects.** Ran `mutagen run --all`
  against [parse](https://github.com/r1chardj0n3s/parse) (1.8k★) and
  [parsy](https://github.com/python-parsy/parsy) (451★) — neither owned by
  the author, both cloned read-only with their pre-existing suites left
  untouched. Scores land at 68% and 75%, well above the 4–24% range seen on
  the author's own code, which is the expected direction: mature, reviewed
  test suites should score higher, not the same. Full survivor-by-survivor
  breakdown, manually verified, in BENCHMARKS.md Run G; raw reports in
  `benchmarks/data/{parse,parsy}_report.{md,json}`.
- Full history is now fetched in `action.yml` instead of `--depth=200`, which
  silently missed the merge-base on branches that diverged from `main` more
  than 200 commits ago.
- The mutant report now surfaces a "multiple exact matches" condition in the
  per-mutant detail instead of discarding it silently after the patch is
  applied.
- `max_tokens` is now part of the LLM cache key — raising the limit used to
  silently replay a reply that was cached under the old, lower limit and
  truncated accordingly.
- `.mutagen/` is now gitignored the moment its cache directory is first
  created, not only if a `.gitignore` already existed — the cache holds raw
  prompts and replies, i.e. the user's own code.
- Docs now warn against `pull_request_target` for the CI mutation gate and
  flag the prompt-injection risk of feeding an LLM-generated report from an
  untrusted PR back into a privileged workflow.
- `[improvement]` Added regression coverage for `AnthropicProvider.complete_json`
  (request shape, cache key, cost accounting, and that temperature/top_p/top_k
  and reasoning are never sent — the current Anthropic models reject them).

## 0.1.1 — 2026-08-14

Fixes from an independent pre-marketplace-launch audit of 0.1.0. Published to
PyPI (0.1.0 stays as-is — PyPI does not allow re-uploading a version).

- **`[security]` pytest subprocesses no longer inherit mutagen's own
  credentials.** `run_pytest` used to hand every mutant's test run the full
  parent environment, including `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY` and
  `GITHUB_TOKEN`. On the GitHub Action the code under test is a pull request's
  own — untrusted — code, which could read a secret out of `os.environ` or
  leak one into an assertion failure that then lands in the PR comment.
  `SECRET_ENV_VARS` is now stripped from every pytest subprocess's
  environment before it starts. Regression test:
  `test_pytest_subprocess_does_not_see_mutagens_secrets`.
- **`coverage` is now a direct dependency**, not just a `dev`-extra transitive
  one. `coverage_map.load()` runs `import coverage` inside *mutagen's own*
  interpreter to read the data file the instrumented baseline wrote — a plain
  `pip install mutagen-cli` never pulled that in, so a user with `pytest-cov`
  correctly installed in their own project venv still hit a silent
  `ImportError`, fell back to the heuristic mapping, and got told to "install
  pytest-cov" even though it already was. Regression test:
  `test_coverage_is_a_direct_dependency_not_only_a_dev_extra`.
- **A prose reply whose first `{...}` fragment isn't the real payload no
  longer produces a silent empty result.** `extract_json`'s raw_decode
  fallback returned the first balanced JSON object it found in free-form
  text, even one missing the caller's required keys (e.g. `mutants`) — the
  generator's `.get("mutants", [])` then quietly returned `[]` and the run
  reported "the model returned no usable mutants" with no indication why.
  `extract_json` now takes the schema's `required` keys and keeps scanning
  (or raises `ProviderError`) until it finds an object that actually has
  them. Regression tests:
  `test_extract_json_rejects_a_wrong_shaped_object_instead_of_going_silent`,
  `test_openrouter_provider_raises_on_reply_missing_the_mutants_key`.
- **`action.yml` hardening:**
  - `pip install mutagen-cli` is now pinned to `==0.1.1`, so the mutation
    gate is reproducible instead of picking up whatever is newest at build
    time.
  - Every `${{ inputs.* }}` / `${{ github.* }}` value used inside a bash
    `run:` block is now passed through `env:` and read back as a shell
    variable, rather than templated directly into the script text.
  - `--effort` is only passed to `mutagen` when `provider: anthropic` — it is
    a dead argument on OpenRouter.
- **Docs synced to the actual repo state:** README's test count (74 → 90,
  after this release's new regression tests) now matches the CHANGELOG's;
  the CHANGELOG's claim that the Action "defaults to anthropic" is corrected
  to `openrouter`, matching `action.yml`; the README's "tested on three
  independent projects" heading is now "three other real projects" — all
  three are the author's own repos, not independent ones; the quickstart
  gains a `mutagen run --all --max-mutants 5` line for trying the tool on a
  clean tree, where a plain `mutagen run` has nothing to diff against and
  prints "Nothing to mutate"; the Anthropic provider section notes that path
  is less battle-tested live than OpenRouter, since every live run recorded
  in BENCHMARKS.md went through OpenRouter.

## 0.1.0 — 2026-08-13

Published to PyPI: <https://pypi.org/project/mutagen-cli/0.1.0/>.

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
- The GitHub Action takes a `provider` input (defaults to `openrouter`, same
  as the CLI) and an `openrouter-api-key` input; its `model` input now
  defaults to the provider's default.
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

- **`[improvement]` Relevant tests only.** A mutant runs just the tests that
  reach it, not the whole suite. Originally a filename/symbol heuristic; now
  measured from coverage, with that heuristic kept as the fallback — see
  "Coverage-based test mapping" below.
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
- 86 tests for mutagen itself. They run offline and for free — the pipeline
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

### Coverage-based test mapping

- **Which tests run against a mutant is now measured, not guessed.** The
  baseline run — already needed to prove the suite is green — runs under
  `pytest-cov` with `--cov-context=test`, producing a per-line map of the tests
  that executed it. Each mutant is then run against the tests covering exactly
  the lines *that mutant* changed. This closes the residual risk left open by
  D10: a killing test in a file the filename/symbol heuristic never picked used
  to produce a false survivor. Regression test
  `test_the_heuristic_misses_the_killing_test` pins the failing case, and
  `test_coverage_mapping_finds_the_killing_test` shows the same mutant killed.
  See DECISIONS.md D11.
- **New report category: `unreached`.** When nothing executes the mutated
  lines, the mutant is not run at all — no test outcome can depend on code no
  test reaches — and is reported in its own section. It stays a `survived`
  verdict, so the mutation score keeps exactly the shape it had. On the polygon
  this surfaces `Page.has_prev`, which none of the 36 tests ever reads.
  `usage`-style fields `test_mapping` and `no_coverage` are in the JSON report.
- **The heuristic is a fallback, not a removal.** Without `pytest-cov` in the
  interpreter that runs the tests, mapping falls back to the heuristic and says
  so — `mapping: heuristic (install pytest-cov for precise coverage mapping)` —
  in the terminal, the markdown report and the JSON. `--dry-run` stays on the
  heuristic, since it must not run your suite, and labels itself accordingly.
- **`[improvement]` coverage's default core silently loses contexts.**
  `COVERAGE_CORE=sysmon`, the default on Python 3.12+, disables line events
  once a line has been seen, so only the *first* test to reach a line is
  recorded against it. Measured on the polygon: `victim/pricing.py:16` came
  back with 1 context under `sysmon` and 5 under `ctrace`. A map like that
  would send mutagen to run a strict subset of the covering tests — the same
  false-survivor bug in a much subtler form. The instrumented run forces
  `COVERAGE_CORE=ctrace`, guarded by
  `test_every_covering_test_is_recorded_not_just_the_first`.
- **`[improvement]` Generation now happens after the baseline**, because the
  prompt carries the covering tests. Side effect worth having: a red suite is
  found before any money is spent instead of after.
- Baseline cost of instrumentation on the polygon (36 tests): 0.25 s → 0.35 s,
  paid once per run. Per-mutant runs get faster — the selection is test node
  ids rather than whole files. Numbers in BENCHMARKS.md Run F.

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
- Coverage mapping (Run F, 2026-08-13): Runs A and B re-measured with the
  coverage-based mapping. Verdict counts identical (28 mutants 12/16; 43
  mutants 8/35) — the polygon's layout is one the heuristic already got right
  — with 1 and 5 survivors respectively reclassified as `unreached`.
- Known gap found by the measurement: the model clusters several mutants on one
  line (3 on `__len__`'s single `return`), reporting one blind spot repeatedly.
  Deduplicating survivors by mutated line would tighten the report.
