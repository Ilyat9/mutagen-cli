# mutagen

**Your tests are green. Here's what they don't catch.**

mutagen introduces realistic bugs into your code — off-by-one errors, missed
cache invalidation, swapped arguments, inverted conditions — and reruns your
test suite. Any bug that survives is a gap in your tests, reported as the
concrete failure your users will hit.

Unlike classic mutation testing, the mutants are written by an LLM that has
read both your function *and* the tests covering it, so it aims at the blind
spots instead of flipping operators at random.

Built for the case where you (or Claude Code, or Cursor) just wrote a pile of
code and a pile of tests, and you want to know whether the tests mean anything.

Real output, from the sample project in `tests/fixtures/victim_project` — 36
tests, all green, half of them deliberately worthless:

```
mapping: coverage (15/15 function(s) mapped from a 0.5s instrumented baseline; 1 reached by no test)

╭───────────────────────────────────────────────────────╮
│ mutation score  43%   (12 killed / 28 viable mutants) │
╰───────────────────────────────────────────────────────╯

file                  killed  survived  score
victim/cache.py            3         5    38%
victim/pagination.py       4         3    57%
victim/parsing.py          2         3    40%
victim/pricing.py          1         3    25%
victim/text.py             2         2    50%

15 bugs your tests would not catch

╭──────────────────────────── survivor 11 ─────────────────────────────╮
│ the discount cap acts as a floor, so capped promotions give away     │
│ more than intended                                                   │
│ victim/pricing.py::apply_discount   [wrong_operator]                 │
╰──────────────────────────────────────────────────────────────────────╯
--- victim/pricing.py
+++ victim/pricing.py (mutated)
@@ -19,4 +19,4 @@
     if max_discount is not None:
-        discount = min(discount, max_discount)
+        discount = max(discount, max_discount)

1 mutant(s) in code no test executes at all
Not a weak assertion — an absence. No test reaches these lines, so nothing here
could ever have failed.

╭─────────────────────────────── unreached 1 ────────────────────────────────╮
│ the first page reports a previous page, so back buttons render on page one │
│ victim/pagination.py::Page.has_prev   [boundary_condition]                 │
╰────────────────────────────────────────────────────────────────────────────╯
--- victim/pagination.py
+++ victim/pagination.py (mutated)
@@ -18,5 +18,5 @@
     @property
     def has_prev(self):
-        return self.page > 1
+        return self.page >= 1

12 killed  16 survived (1 of them unreached by any test)
```

Reproduce it yourself: `python scripts/benchmark.py` (offline, deterministic,
zero API calls — it never touches the network without an explicit `--live`).

Pointed at the same project with **model-written** mutants, it produced 43
mutants across 15 functions: 8 killed, 35 survived, **0 unapplicable**, and of
the 35 survivors only 2 were junk (5.7%). They covered 18 of the 22 test blind
spots documented for that project — plus 7 the project's own notes had missed.
Full numbers and caveats in [BENCHMARKS.md](BENCHMARKS.md).

Live over the OpenRouter API (2026-08-13, `--invent` on):
`anthropic/claude-sonnet-5` produced 40 mutants with **0% unapplicable** and
12.9% junk survivors for **$0.26**; `anthropic/claude-opus-5` — 0%
unapplicable, 3.0% junk, 14/22 blind spots, **$0.68**. Details in
[BENCHMARKS.md](BENCHMARKS.md) Run D.

## Quickstart

Not on PyPI yet — install from source. The distribution name is reserved as
`mutagen-cli` (`mutagen` itself is the audio-metadata library); the command is
`mutagen`.

```bash
git clone https://github.com/mutagen-cli/mutagen && cd mutagen && pip install -e .
```

```bash
export OPENROUTER_API_KEY=sk-or-...
```

```bash
mutagen run
```

That's it. No config file. `mutagen run` diffs your working tree against `main`,
mutates only the functions you changed, and runs only the tests that actually
cover them. See [Providers](#providers) if you'd rather talk to Anthropic directly.

## Providers

mutagen supports two LLM providers, switched with `--provider`:

**OpenRouter (default).** An OpenAI-compatible gateway that carries the same
Claude models — useful because the Anthropic API does not serve every region.
OpenRouter works from Russia without a VPN.

1. Create a key at <https://openrouter.ai/keys>.
2. `export OPENROUTER_API_KEY=sk-or-...`, or put
   `{"openrouter_api_key": "sk-or-..."}` in `.mutagen/config.json`.

Default model: `anthropic/claude-sonnet-5` — the best price/quality point for
mutant generation ($2/M input, $10/M output as of 2026-08-13). Override with
`--model`, e.g. `--model anthropic/claude-opus-5`.

**Anthropic.** Direct API access.

1. `export ANTHROPIC_API_KEY=sk-ant-...`, or put
   `{"anthropic_api_key": "sk-ant-..."}` in `.mutagen/config.json`.
2. Run with `--provider anthropic`. Default model: `claude-opus-5`.

Two provider-specific behaviours worth knowing:

- The Claude 5 models on OpenRouter run with **reasoning enabled by default**,
  which pollutes the JSON reply and inflates cost. mutagen explicitly sends
  `reasoning: {"enabled": false}` on every request. To opt back in, set
  `{"openrouter_reasoning": true}` in the config file.
- Sampling parameters (`temperature` and friends) are silently ignored by those
  models, so the OpenRouter provider never sends them.

Cost is computed from the API's usage fields against a built-in price table.
Override it or price an unlisted model via `{"prices": {"model/id":
[input_per_mtok, output_per_mtok]}}` in the config; a model with no known price
is reported as "cost unavailable" rather than $0.

## Usage

```bash
mutagen run                          # only what changed vs main
mutagen run --base develop           # ...vs another branch
mutagen run --path src/billing.py    # specific files or directories
mutagen run --all                    # the whole codebase
mutagen run --dry-run                # show the plan and the test mapping, spend nothing
```

Turning survivors into tests:

```bash
mutagen run --invent          # print a test that would catch each survivor
mutagen run --invent-apply    # ...and save the verified ones to tests/mutagen_generated/
```

Every suggested test is checked twice before you see it: it must pass against
your real code and fail against the mutant. Suggestions that fail either check
are still shown, but labelled — the feature does not get to lie to you.

For CI:

```bash
mutagen run --report-md report.md --report-json report.json --fail-under 70
```

### GitHub Action

`action.yml` in this repo is a mutation gate for pull requests. It mutates only
what the PR changed and posts the survivors as a comment, editing the same
comment on each push instead of piling up new ones.

```yaml
name: mutation
on: pull_request

jobs:
  mutagen:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e .[dev]
      - uses: mutagen-cli/mutagen@v0
        with:
          provider: anthropic          # or openrouter
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          # openrouter-api-key: ${{ secrets.OPENROUTER_API_KEY }}
          fail-under: "70"
          invent: "true"
```

### Options that matter

| Flag | Default | |
| --- | --- | --- |
| `--max-mutants N` | 25 | Hard ceiling on how many mutants to generate. |
| `--max-files N` | 20 | Hard ceiling on files considered. |
| `--timeout SECS` | 30 | Per-mutant budget. Raised automatically if your suite is slow. |
| `--workers N` | CPUs/2 | Mutants run in parallel, each in its own copy of the repo. |
| `--provider NAME` | `openrouter` | `openrouter` or `anthropic`. See [Providers](#providers). |
| `--model ID` | provider's default | `anthropic/claude-sonnet-5` on OpenRouter, `claude-opus-5` on Anthropic. |
| `--effort LEVEL` | `medium` | `low`…`max`. Lower is cheaper and faster. Anthropic only. |
| `--no-cache` | off | Ignore the on-disk cache in `.mutagen/cache/`. |
| `--python PATH` | project venv | Interpreter used to run your tests. |

LLM responses are cached on disk per function, so re-running after editing one
function only pays for that function. The cache key includes the tests shown to
the model, so a change in coverage correctly misses the cache.

## What the verdicts mean

| Verdict | Meaning |
| --- | --- |
| **killed** | A test failed. Good — the bug would have been caught. |
| **survived** | Every test still passed. This is a hole in your suite. |
| **timeout** | The mutant probably created an infinite loop. Counted separately, not as a kill. |
| **survived (unreached)** | A survivor of a stronger kind: no test executes the mutated lines at all, so nothing could ever have failed. Needs a coverage map; scored as a survivor. |
| **unapplicable** | The edit couldn't be placed in the file, or didn't parse. Excluded from the score entirely. |
| **error** | pytest could not run (collection error, no tests). Excluded from the score. |

Mutation score is `killed / (killed + survived)`. Timeouts, errors, and
unapplicable mutants are deliberately kept out of the numerator and the
denominator — counting them as kills would flatter the score for no reason.

## Requirements

- Python 3.10+
- `pytest-cov` in the interpreter that runs your tests, for coverage-based test
  mapping. Optional — without it mutagen falls back to a heuristic and says so.
- A pytest suite that currently passes. mutagen checks this first and refuses to
  run against a red suite, because every mutant would look "killed".
- An OpenRouter API key in `OPENROUTER_API_KEY` (get one at
  <https://openrouter.ai/keys> — works from Russia without a VPN), or an
  Anthropic key in `ANTHROPIC_API_KEY` with `--provider anthropic`. Keys can
  also live in `.mutagen/config.json`.

macOS and Linux are tested. Windows is not.

### Working on mutagen itself

```bash
pip install -e ".[dev]" && pytest && ruff check .
```

74 tests, all offline and free: the pipeline tests drive real pytest
subprocesses through a replay provider, and the CLI tests pre-seed the disk
cache so no API key is involved.

## Limitations

- **Python and pytest only.** No other languages or runners.
- **Cost is real.** One LLM call per changed function, plus one per survivor
  with `--invent`. The disk cache means iteration is cheap, but the first run
  on a large diff is not free. Use `--dry-run` to see the call count first.
- **Equivalent mutants still slip through.** The prompt works hard to forbid
  mutations that don't change behaviour, and most of what remains is real, but
  not all of it. A "survivor" is a lead to investigate, not a proven gap.
- **Precise test mapping needs `pytest-cov`** in the interpreter that runs your
  tests. With it, mutagen measures which tests execute which lines. Without it
  it falls back to a filename/symbol heuristic and says so in the report — and
  a heuristic that picks the wrong files reports mutants as survivors when the
  test that would kill them simply never ran.
- **Each worker copies your repo** into a temp directory. Large repos with
  large untracked directories will feel that.
- **Your working tree is never touched** — except by `--invent-apply`, which
  writes new files under `tests/mutagen_generated/` and nowhere else.
- **Not a coverage tool.** A high mutation score on the functions you changed
  says nothing about the functions you didn't.

## How it works

1. `git diff` against the merge base → changed line ranges (committed *and*
   uncommitted, plus untracked files).
2. `ast` maps those lines to whole functions, so the model sees complete units.
3. Your suite runs once, unmutated, under `coverage` with a per-test context.
   That single run does two jobs: it proves the suite is green before any money
   is spent, and it produces the map of **which tests execute which lines**.
   Without `pytest-cov` installed, mutagen falls back to a filename/symbol
   heuristic and labels the report `mapping: heuristic`.
4. Each function is sent to the model together with the tests that actually
   cover it, with instructions to produce bugs those tests look least likely to
   catch. Responses are constrained to a JSON schema.
5. Mutations come back as SEARCH/REPLACE blocks (not diffs — models get line
   numbers wrong). They are applied exactly where possible, then with
   whitespace and indentation normalisation, then fuzzily via `difflib` — and
   always **inside the target function's own line range**, so a block that also
   occurs in a neighbouring function cannot silently mutate that one instead.
   Blocks that can't be placed there, or that produce code which doesn't parse,
   are marked unapplicable rather than guessed at.
6. Each mutant runs in a worker's private copy of the repo, against exactly
   the tests that execute the lines it changed, with a timeout. A mutation on
   lines **no** test executes is not run at all — nothing could depend on it —
   and is reported as `unreached` in its own section.

## License

MIT
