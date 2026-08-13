# Benchmarks

Everything here is reproducible from a clean checkout. Where a number has not
been measured, it says so rather than being estimated.

## Run A — engine validation (offline, deterministic)

> **Superseded for the mapping question by [Run F](#run-f--coverage-based-test-mapping-2026-08-13).** The verdict counts below still reproduce exactly; what changed is that one of the 16 survivors is now reported as *unreached* rather than merely surviving.

```bash
python scripts/benchmark.py
```

Fixed mutant set (`tests/fixtures/canned_mutants.json`, 28 hand-written mutants)
pre-seeded into the disk cache, so this makes **zero API calls** and produces
identical output on every run. It measures the engine — SEARCH/REPLACE
application, sandboxing, parallel execution, verdict classification — against
the golden standard in
[`tests/fixtures/victim_project/WEAK_TESTS.md`](tests/fixtures/victim_project/WEAK_TESTS.md).

Machine: macOS (Darwin 25.5.0), Python 3.14.6, 1 worker per available pair of
CPUs. Target: the 36-test polygon, all green at baseline. Re-verified on
2026-08-13 from a clean `rsync` copy with a fresh venv: identical verdicts.

| | |
| --- | --- |
| Mutants | 28 |
| killed | 12 (42.9%) |
| survived | 16 (57.1%) |
| timeout | 0 |
| error | 0 |
| unapplicable | 0 |
| **Mutation score** | **42.9%** |
| Wall time | 2.4–3.0 s |
| LLM calls | 15 (15 served from cache) |
| Cost | $0.0000 |
| **Verdicts matching the golden standard** | **28 / 28** |

Every verdict matched the prediction made by reading the test suite by hand.

### Blind-spot coverage

Of the 22 documented blind spots in `WEAK_TESTS.md`, the 16 survivors point at
16 of them:

| Covered | B1, B2, B3, B4, B5, B6, B7, B8, B11, B12, B13, B16, B17, B18, B20, B21 |
| --- | --- |
| Not covered by this mutant set | B9 (`per_page < 1`), B10, B14, B15, B19, B22 |

So the fixed set exercises **16/22 ≈ 73%** of the known blind spots. The six it
misses are a property of the hand-written set, not of the engine — a live run
generates different mutants.

### What this run does *not* measure

- **Unapplicable rate.** It is 0% by construction: the canned blocks were copied
  out of the source. The real number depends on model output — see Run B, and
  Run C for the figure that actually counts.
- **Precision / equivalent-mutant rate.** Also 0% by construction. Every canned
  mutant is a real behavioural change, hand-checked. Run B measures this against
  model-written mutants.
- **Cost and latency of generation.** No API calls happen.

## Run B — mutant quality, model-written, offline

> **Superseded for the mapping question by [Run F](#run-f--coverage-based-test-mapping-2026-08-13).** Counts reproduce exactly; 5 of the 35 survivors are now reported as *unreached*.

```bash
python scripts/dump_prompts.py --out prompts/
# a Claude Sonnet agent answered all 15 prompts into benchmarks/data/live_replies.json
python scripts/benchmark.py --replies benchmarks/data/live_replies.json --max-mutants 50 \
    --save-report benchmarks/data/live_report.json
```

Measured 2026-08-13. The 15 real per-function prompts were dumped verbatim and
answered by a Claude Sonnet subagent acting as the provider, then fed back
through the production pipeline. **The mutants are genuinely model-written**;
what is missing is the HTTP path (see caveats below).

The agent was explicitly barred from reading `canned_mutants.json`,
`WEAK_TESTS.md`, and this file, so the coverage and precision numbers are not
contaminated by the answer key.

| Metric | Target | Measured |
| --- | --- | --- |
| Mutants generated | — | **43** across 15/15 functions (2–3 each) |
| killed | — | 8 (18.6%) |
| survived | — | 35 (81.4%) |
| timeout / error | — | 0 / 0 |
| Mutation score | — | **18.6%** |
| **Unapplicable rate** | **< 15%** | **0%** (0/43) — see caveat |
| **Equivalent / junk survivors** | **< 20%** | **5.7%** (2/35) |
| Blind spots covered (of 22) | — | **18 (82%)** |
| New blind spots found, not in WEAK_TESTS.md | — | **7** |
| `--invent` tests reaching `verified` | — | not run (needs a second generation round) |
| Wall time (execution only) | — | 3.7 s |
| Input / output tokens | — | not measurable offline |
| Cost | — | **$0.00** |

**Prompt iterations required: 0.** Both spec thresholds passed on the first
attempt, so `src/mutagen_cli/prompts.py` was not changed. Had either been
breached, each iteration would be a new row here.

### Precision: which survivors are junk

Every one of the 35 survivors was read by hand. **Zero equivalent mutants** —
each one changes observable behaviour for some reachable input. Two are junk on
the weaker criterion, "no reasonable test would assert on this":

| Survivor | Why it is junk |
| --- | --- |
| `LRUCache.__init__` — default capacity 128 → 100 | Changes a default, not behaviour. Nobody writes a test pinning the default cache size. |
| `LRUCache.invalidate` — returns `None` instead of `False` for an absent key | Borderline. It does violate the documented contract, but only an `is False` check distinguishes it. Counted as junk to be conservative. |

**2/35 = 5.7%, well under the 20% threshold.**

Separately — not junk, but worth noting — the model **clusters mutants on the
same line**: 3 on `__len__`'s single `return`, 3 each on `has_next`, `has_prev`,
and `invalidate_prefix`. Each is a distinct real bug, but they report one blind
spot three times. Deduplicating survivors by mutated line would make the report
shorter without losing information.

### ⚠️ Why the 0% unapplicable rate is optimistic

The agent could open `victim/*.py` to confirm exact whitespace before writing a
`search_block`. **A model called over the API cannot do that** — it sees only the
function text embedded in the prompt and must reproduce indentation from memory.
Misquoted `search_block`s are the single largest source of unapplicable mutants
in practice, so treat 0% as a *floor*, not the expected live figure. This is the
main reason Run C is still worth doing.

### Blind-spot coverage — 18 of 22

| Covered (18) | B1, B2, B3, B4, B5, B6, B7, B8, B9, B11, B12, B13, B15, B16, B18, B20, B21, B22 |
| --- | --- |
| **Not covered (4)** | **B10, B14, B17, B19** |

Why the four were missed:

- **B10** (page ≥ 2 offsets are only length-checked) — needs a mutation that
  changes behaviour *only* from page 2 onward, e.g. `if page > 1: start += 1`.
  Every offset mutation the model wrote also breaks page 1, so
  `test_paginate_first_page` killed it. This is the one genuinely interesting
  miss: the model does not reason about *which* input a test happens to use.
- **B14** (empty-key rejection in `parse_key_values`) — the model spent its 3
  slots on `=`-splitting, whitespace, and duplicate-key precedence, all of which
  are higher-value. A budget artefact, not a capability gap.
- **B17** (negative-percentage rejection) and **B19** (`price is None` /
  `price < 0`) in `apply_discount` — same cause: only 3 mutants per function,
  and the model prioritised the cap direction, `percent=None`, and rounding.

Raising `--max-mutants` so `mutants_per_target` yields 5–8 instead of 3 would
likely close B14, B17, and B19. B10 needs a prompt change.

### 7 blind spots the polygon's own documentation missed

The model found real gaps that `WEAK_TESTS.md` does not list — evidence the
tool finds things a careful human author did not:

| Gap | Function |
| --- | --- |
| `__len__` is never asserted on at all | `LRUCache.__len__` |
| Overwriting an existing key does not refresh recency | `LRUCache.set` |
| `invalidate()`'s return value for an *absent* key | `LRUCache.invalidate` |
| `items=None` handling | `paginate` |
| `Page.total` reflecting the slice instead of the collection | `paginate` |
| Trailing number without a unit must raise | `parse_duration` |
| `percent=None` meaning 0%, not 100% | `apply_discount` |

`WEAK_TESTS.md` should be extended with these.

### What Run B still does not measure

Tokens, dollars, generation latency, and the structured-output path
(`output_config.format`). Those require real HTTP calls — that is Run C.

## Run C — live API

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python scripts/benchmark.py --live --invent --save-report live_api_report.json
```

**Not measured.** No Anthropic credentials are available in this environment.
Attempted once on 2026-08-13; the log is in `benchmarks/data/benchmark_live.log`:

```
warning: victim/cache.py::LRUCache.__init__: no API key found. Set ANTHROPIC_API_KEY,
         or put {"api_key": "..."} in .mutagen/config.json
error: the model returned no usable mutants.
```

Left to fill in: real unapplicable rate (expected above Run B's 0% — see the
caveat), input/output tokens, dollar cost, generation wall time, and the
`--invent` verified/rejected/weak split. **All of these were measured over
OpenRouter instead — see Run D below.**

**Estimated cost when run:** ~15 generation calls + ~1 per survivor. At
`claude-opus-5` / `effort=medium`, roughly **$2–4** per pass.

If the unapplicable rate exceeds 15% or junk survivors exceed 20%, the fix is
prompt iteration (`src/mutagen_cli/prompts.py`), then re-run — each iteration as
a new row above. The cache key includes the prompt text, so an edited prompt
misses the cache on its own; `--no-cache` is not needed.

**Judging "junk"**: a survivor is junk if the mutated code cannot produce
different observable behaviour from the original for any reachable input
(an equivalent mutant), or if it mutates something no reasonable test would
assert on. Count these by hand; the number is only credible if a person read
every survivor.

## Run D — live API via OpenRouter

```bash
export OPENROUTER_API_KEY=sk-or-...
python scripts/benchmark.py --live --invent --provider openrouter \
    --model anthropic/claude-sonnet-5 --save-report benchmarks/data/live_sonnet5.json
python scripts/benchmark.py --live --invent --provider openrouter \
    --model anthropic/claude-opus-5 --save-report benchmarks/data/live_opus5.json
```

Measured **2026-08-13** with a real OpenRouter key, `--invent` on, cold cache.
Both models ran with `reasoning: {"enabled": false}` (the OpenRouter default
is *on* for them — see DECISIONS.md D8) and no sampling parameters. Reports:
`benchmarks/data/live_sonnet5.json` / `benchmarks/data/live_opus5.json`, full
logs in `benchmarks/data/live_sonnet5.log` / `benchmarks/data/live_opus5.log`.

| Metric | anthropic/claude-sonnet-5 | anthropic/claude-opus-5 |
| --- | --- | --- |
| Mutants generated | 40 across 15/15 functions | 40 across 15/15 functions |
| **Unapplicable rate** | **0%** (0/40) | **0%** (0/40) |
| killed / survived | 9 / 31 | 7 / 33 |
| Mutation score of the polygon | 22.5% | 17.5% |
| **Junk survivors (hand-checked)** | **4/31 (12.9%)** | **1/33 (3.0%)** |
| `--invent` verified / rejected / weak | 29 / 0 / 2 | 31 / 0 / 2 |
| Input / output tokens | 72,947 / 11,552 | 74,442 / 12,110 |
| Cost | **$0.2614** | **$0.6750** |
| Wall time (generation + execution + invent) | 232.7 s | 266.2 s |
| Documented blind spots covered (of 22) | 11 | 14 (13 without the junk one) |

**Prompt iterations required: 0.** Unapplicable (threshold 15%) and junk
(threshold 20%) both passed on the first attempt for both models.

Precision was judged by hand — every survivor's diff was re-read against the
polygon source. Junk found:

| Model | Survivor | Why it is junk |
| --- | --- | --- |
| sonnet-5 | `LRUCache.__init__#3` — default `capacity=128 → 1` | Changes a default nobody pins with a test (same precedent as Run B). |
| sonnet-5 | `LRUCache.invalidate#2` — `del d[key]` → `d.pop(key)` | Equivalent: binary-identical on an OrderedDict. Its `--invent` test came back `weak` — correctly. |
| sonnet-5 | `parse_duration#3` — error message wording | Equivalent: both variants raise `ValueError`; only the text differs. |
| sonnet-5 | `page_count#2` — `total <= 0` → `total < 0` | Observable only for negative `total`, a degenerate input no reasonable test pins. |
| opus-5 | `parse_key_values#3` — redundant `value.strip("\n")` | Equivalent: the value is fully stripped two lines later. Also `weak` under `--invent`. |

Note how often the `--invent` self-check flags junk on its own: 3 of the 5
junk survivors produced a `weak` suggested test.

Both models spent their whole `truncate` budget elsewhere (B20–B22 uncovered),
and opus-5 additionally left B12/B14/B17/B18/B19 alone. Opus-5 found more
blind spots with much less junk; on this polygon it is the better generator,
at 2.6× the price. Sonnet-5 at $0.26 per full pass remains the sensible
default.

### What changed vs Run B (subagent-written mutants)

- **Unapplicable stayed 0%** even over HTTP, against Run B's caveat that the
  offline figure was a floor. Structured outputs plus exact SEARCH/REPLACE
  blocks hold up on the wire.
- Junk rate rose from 5.7% to 12.9% for sonnet-5 — the offline answering agent
  could verify whitespace against the source and self-correct; the raw model
  cannot. Opus-5's 3.0% is *better* than Run B's agent-assisted number.

## Run E — pre-publication audit, 2026-08-13

An external re-check of everything above from a clean copy (fresh `rsync`
without `.venv`/`.mutagen`/caches, new virtualenv, `pip install -e ".[dev]"`).

| Check | Result |
| --- | --- |
| Project test suite | **74 passed** (was 61 before the audit's regression tests) |
| `ruff check .` | clean |
| Run A reproduced | **28 mutants, 12/16, 42.9%, 28/28 golden verdicts** — identical |
| Run B reproduced (`--replies benchmarks/data/live_replies.json`) | **43 mutants, 8/35, 18.6%, 0% unapplicable** — identical |
| `mutagen run --all --dry-run` on mutagen itself | 94 functions in 13 files, plan printed, 9 calls quoted, nothing spent |
| Live mini-run, OpenRouter, `--max-mutants 5` | 5 mutants, 0% unapplicable, 2 calls, **$0.014**, 14.7 s |
| 3 hand-written must-die mutants through the CLI | all 3 `killed`, each naming its own failing test |
| Live `--invent` on `victim/pricing.py` | 3 mutants, 2 `verified` suggested tests, 3 calls, **$0.016**, 33.1 s |

The two live runs used `anthropic/claude-sonnet-5` and are the cheapest
end-to-end proof that the HTTP path, the structured-output path, cost
accounting and `--invent` all still work; they are not a quality measurement
(Run D is).

The audit changed engine behaviour in one way that could have moved the
numbers — SEARCH/REPLACE blocks are now confined to the target function's line
range — and it did not: Runs A and B reproduce byte-identically, because every
canned and model-written block already matched inside its own function. The
fix removes a failure mode that had not yet fired on this polygon, not a
result. See CHANGELOG.

## Run F — coverage-based test mapping, 2026-08-13

**Runs A and B above are superseded by this section for the mapping question**;
their verdict counts still stand (see below — they did not move).

`mutagen run` now maps functions to tests by measuring, not guessing: the
baseline suite runs once under `pytest-cov` with `--cov-context=test`, and each
mutant is then run against the tests that execute the lines it changed.

```bash
python scripts/benchmark.py                                  # Run A, remapped
python scripts/benchmark.py --replies benchmarks/data/live_replies.json --max-mutants 50
```

| | Run A (heuristic) | Run A (coverage) | Run B (heuristic) | Run B (coverage) |
| --- | --- | --- | --- | --- |
| Mutants | 28 | 28 | 43 | 43 |
| killed | 12 | 12 | 8 | 8 |
| survived | 16 | 16 | 35 | 35 |
| — of which **unreached** | n/a | **1** | n/a | **5** |
| unapplicable | 0 | 0 | 0 | 0 |
| **Mutation score** | 42.9% | **42.9%** | 18.6% | **18.6%** |
| Golden-standard verdicts | 28/28 | **28/28** | — | — |

**The score did not move, and that is the honest result.** The polygon has a
textbook layout — `victim/cache.py` ↔ `tests/test_cache.py` — which is exactly
the case the filename heuristic gets right. Coverage cannot improve on a
mapping that was already correct; what it does is stop the mapping from being a
guess. The regression test
`test_the_heuristic_misses_the_killing_test` builds the layout where the
heuristic *does* fail (the killing test names neither the function nor its
module, three vacuous decoys outrank it) and shows the mutant reported as a
survivor under the heuristic and `killed` under coverage.

What did change is the *shape* of the report. Mutants in code no test executes
are now separated out:

| Run | Unreached mutants | Where |
| --- | --- | --- |
| A | 1 | `victim/pagination.py::Page.has_prev` |
| B | 5 | `Page.has_prev`, plus 4 more on the same property |

`Page.has_prev` is never read by any of the 36 tests. Under the old report that
was one survivor among sixteen; now it is a different kind of finding —
not "your assertion is too weak" but "nothing runs this code at all".

### Cost of the instrumented baseline

Measured on the polygon (36 tests), best of three:

| | Baseline duration |
| --- | --- |
| Plain | 0.25 s |
| With `--cov --cov-context=test` | 0.35 s |

About +40% on the one baseline run, ~0.1 s absolute here. It is paid once per
`mutagen run`: the code under test is identical for every mutant, so the map is
built on the baseline and reused. Per-mutant runs get *faster*, because the
selection is node ids rather than whole files.

### ⚠️ coverage's default core silently loses contexts

`COVERAGE_CORE=sysmon` — the default on Python 3.12+ — disables line events
once a line has been seen, so only the **first** test to reach a line is
recorded against it. Measured on the polygon, `victim/pricing.py:16` came back
with 1 context under `sysmon` and 5 under `ctrace`, with the same suite.

A map like that is worse than no map: it would send mutagen to run a strict
subset of the covering tests and report false survivors — the exact bug this
feature exists to remove, in a form much harder to notice. mutagen therefore
forces `COVERAGE_CORE=ctrace` for the instrumented run, and
`test_every_covering_test_is_recorded_not_just_the_first` fails if that is
removed.

## Run G — external repos, third-party review baseline, 2026-08-14

Runs A–F all measure mutagen against code the author wrote or owns. This run
checks the other direction: does the score move in the expected direction on
code that has years of independent review behind it, from projects the author
does not own and did not touch?

Two candidates, picked from GitHub by stars/domain/license fit, cloned
read-only, installed into a fresh venv each, `pytest` run unmodified before
touching mutagen:

| Repo | Stars | License | Scope | Pre-existing suite |
| --- | --- | --- | --- | --- |
| [r1chardj0n3s/parse](https://github.com/r1chardj0n3s/parse) — reverse of `str.format()` | 1.8k | MIT | `parse/__init__.py` (46 functions) | 99 passed, 1 skipped |
| [python-parsy/parsy](https://github.com/python-parsy/parsy) — parser combinators | 451 | MIT | `src/parsy/__init__.py` (64 functions) | 86 passed, 2 skipped |

```bash
mutagen run --all --path parse/__init__.py --report-md report.md --report-json report.json
mutagen run --all --path src/parsy/__init__.py --report-md report.md --report-json report.json
```

| | parse | parsy |
| --- | ---: | ---: |
| Mutants generated | 25 | 25 |
| killed | 17 | 18 |
| survived | 8 | 6 |
| — of which unreached | 1 | 0 |
| timeout | 0 | 1 |
| **Mutation score** | **68%** (17/25) | **75%** (18/24) |
| LLM calls | 11 | 10 |
| Cost | $0.1325 | $0.1288 |
| Duration | 69.1 s | 94.5 s |
| Mapping | coverage | coverage |

Both scores land far above the 4–24% range Runs A–F see on the author's own
projects. That is the expected result, not a surprise to explain away:
mutation score should track the quality and age of the test suite, and both
of these libraries have had years of independent contributors and code review
behind their tests. A method that produced ~20% on everything regardless of
the target would not be measuring anything.

### Survivors, verified by hand

Every survivor below was reviewed manually against the diff and the
project's own test suite before being counted as real. `report.json` in
[`benchmarks/data/parse_report.json`](benchmarks/data/parse_report.json) and
[`benchmarks/data/parsy_report.json`](benchmarks/data/parsy_report.json) has
the full machine output; `parse_run.log` / `parsy_run.log` in the same
directory are the unedited CLI transcripts.

**parse — 7 real, 1 unreached:**

| # | Function | Kind | Verdict |
| --- | --- | --- | --- |
| 1 | `int_convert.__call__` | off_by_one | real — signed `0x`/`0o`/`0b` literals mis-detect their base |
| 2 | `convert_first.__call__` | empty_input | real — empty string now short-circuits to `None` before the user's converter runs |
| 3 | `FixedTzOffset.__init__` (offset) | wrong_operator | real — offset only applied when non-zero, degenerate case wrong |
| 4 | `FixedTzOffset.__init__` (sign) | wrong_operator | real — offset sign flipped, east/west swapped |
| 5 | `FixedTzOffset.__init__` (name) | other | real — name silently uppercased |
| 6 | `FixedTzOffset.utcoffset` (+1min) | wrong_operator | real — UTC offset shifted by a constant minute |
| 7 | `FixedTzOffset.utcoffset` (→None) | missing_return | real — offset always reported as `None` |
| 8 | `FixedTzOffset.tzname` | wrong_default | **unreached** — no test in the suite calls `tzname()` at all; not a weak assertion, an absence |

6 of the 7 real survivors sit in one class, `FixedTzOffset` — the suite
exercises `parse()`'s datetime formats but never asserts on the tzinfo object
those formats construct. The 7th is unrelated: an off-by-one in the numeric
base-prefix detector for signed integers.

**parsy — 6 real, 1 timeout:**

| # | Function | Kind | Verdict |
| --- | --- | --- | --- |
| 1 | `ParseError.line_info` | swallowed_error | real — narrow `except (TypeError, AttributeError)` widened to bare `Exception` |
| 2 | `ParseError.__str__` | boundary_condition | real — `== 1` weakened to `<= 1`, breaks the zero-expected case |
| 3 | `Result.success` | wrong_default | real — furthest-failure index leaks the success index instead of `-1` |
| 4 | `Result.failure` | wrong_default | real — `None` expected value still gets wrapped into the set |
| 5 | `Result.aggregate` | none_handling | real — `not other` narrowed to `other is None`, drops falsy-but-present results |
| 6 | `Parser.__init__` | wrong_default | real — falsy `wrapped_fn` silently replaced by a shared `string("")` parser |
| — | (unnamed) | — | 1 mutant timed out; no verdict, excluded from the score denominator like every other run |

All 6 real survivors are in the same place: the error-reporting meta-layer
(`ParseError`, `Result`). parsy's own tests assert on successful parses and
on the *shape* of failures, but not on the internal bookkeeping
(`furthest`, `expected`) that only matters once results get merged across
alternatives — exactly the kind of thing a hand-written test suite tends to
under-specify because it never causes a wrong *answer* on its own, only a
worse error message or a rare double-counting bug.

### Caveats

- One module per repo, `--max-mutants 25` (the default) — this is a spot
  check, not a full-repo score. A `--all` run over the whole package would
  likely move both numbers.
- Neither repo's source was touched; nothing was forked, no issue or PR was
  filed. The clones live outside this repository and are not part of it.
  This run is read-only research, not a claim about either project's quality.
- Survivor verdicts above are the author's manual read of each diff against
  the target repo's actual test suite, not an automated check — the same
  standard applied to every other run in this file.

## Determinism

- LLM responses are cached on disk under `.mutagen/cache/`, keyed on
  `(provider, model, mode, system prompt, user prompt, schema)` — where *mode*
  is `effort` on Anthropic and the reasoning on/off toggle on OpenRouter. A
  cached run is exactly reproducible.
- Sampling parameters are not sent (`temperature` and friends are rejected by
  the current Anthropic models), so run-to-run variation on a cold cache is
  whatever the model produces at `effort=medium`. A live benchmark should be
  quoted with its cache directory retained, or run several times and reported as
  a range.
- The polygon is frozen. Changing `victim/` or its tests invalidates every
  number above.
