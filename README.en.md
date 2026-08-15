Full version (Russian): [README.md](README.md)

# mutagen-cli

[![CI](https://github.com/Ilyat9/mutagen-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/Ilyat9/mutagen-cli/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mutagen-cli)](https://pypi.org/project/mutagen-cli/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**Your tests are green. Here's what they don't catch.**

<p align="center">
  <img src="assets/mutagen_cli.gif" alt="Ninja turtles in a sewer: ALL TESTS GREEN... but one got away" width="700">
</p>

---

<p align="center">
  <img src="assets/demo.gif" alt="Real run: 3 bugs found in 1.4 seconds, $0.00 from cache" width="700">
</p>

mutagen-cli introduces realistic bugs into your code — off-by-one errors, missed
cache invalidation, swapped arguments, inverted conditions — and reruns your
test suite. Any bug that survives is a gap in your tests, reported as the
concrete failure your users will hit. The mutants are written by an LLM that
has read both your function *and* the tests covering it, so it aims at the
blind spots instead of flipping operators at random.

Real output, excerpted from a run against a third-party repo
([semantic-plagiarism-detector](https://github.com/Ilyat9/semantic-plagiarism-detector),
44 tests, all green):

<img src="assets/mutagen_report.svg" alt="mutagen run: mutation score 21%, two survivors — a spaCy pipeline cache that doesn't key by language, and swapped classification thresholds" width="900">

## Tested on five other real-world projects

Not just the bundled fixture — real apps with pre-existing, already-green
test suites, no source changes made for the tool's sake. Three are my own
projects; two are unrelated third-party libraries:

| Project | Scope | Score | Cost |
| --- | --- | ---: | ---: |
| [semantic-plagiarism-detector](https://github.com/Ilyat9/semantic-plagiarism-detector) | `core/` (33 functions) | **21%** (5/24) | $0.35 |
| [cityfeed](https://github.com/Ilyat9/cityfeed) — Telegram news-digest bot | `rank/` | **20%** (5/25) | $0.13 |
| [cityfeed](https://github.com/Ilyat9/cityfeed) | `dedup/` | **24%** (6/25) | $0.14 |
| [CogniWeb_Agent](https://github.com/Ilyat9/CogniWeb_Agent) — browser LLM agent | 3 modules, 3 runs | 12% / 5% / 4% | $0.78 |
| [parse](https://github.com/r1chardj0n3s/parse) — reverse of str.format, 1.8k★ | `parse/__init__.py` | **68%** (17/25) | $0.13 |
| [parsy](https://github.com/python-parsy/parsy) — parser combinators, 451★ | `src/parsy/__init__.py` | **75%** (18/24) | $0.13 |

The four owned projects all land under 25% on code that already had human
review and a green CI — a language-blind pipeline cache, swapped threshold
values, boundary conditions at window edges, inverted security checks. Not
one codebase's quirk; the same shape of blind spot each time.

To check this isn't just self-flattery, mutagen-cli was also run against two
unrelated open-source libraries — [parse](https://github.com/r1chardj0n3s/parse)
(the reverse of `str.format()`, 1.8k★, MIT) and
[parsy](https://github.com/python-parsy/parsy) (parser combinators, 451★,
MIT) — both with pre-existing green pytest suites, no source changes made.
Scores there are noticeably higher: 68% and 75%, versus 4–24% on my own
projects — mature code with years of review and more contributors does close
more mutations, which is the expected direction: mutation score should track
coverage quality, not sit at a constant. But real gaps still show up, just
concentrated rather than smeared across the module: in parse, all 7 live
survivors cluster around `FixedTzOffset` (timezone handling is systematically
under-covered) plus one off-by-one on signed hex/octal/binary literals; in
parsy, all 6 sit in the error-reporting meta-logic (`ParseError`/`Result`: a
swallowed exception, a wrong boundary, a lost furthest-index).

## Quickstart

```bash
pip install mutagen-cli
export OPENROUTER_API_KEY=sk-or-...
mutagen run
```

Works off the functions you changed — on a clean tree with no diff against
`main` there is nothing to compare against. For a first look on a clean tree:

```bash
mutagen run --all --max-mutants 5
```

(`mutagen-cli` is the distribution name — `mutagen` itself is the
audio-metadata library; the command installed is `mutagen`.) To work on
mutagen-cli itself instead: `git clone https://github.com/Ilyat9/mutagen-cli
&& cd mutagen-cli && pip install -e ".[dev]"`.

No config file. `mutagen run` diffs your working tree against the
repository's default branch (auto-detected from `origin/HEAD`, falling back
to a local `main`/`master`), mutates only the functions you changed, and
runs only the tests that actually cover them. If your default branch is
something else (`develop`, `trunk`, ...) and auto-detection doesn't find it,
say so explicitly: `mutagen run --base master` (or whatever your branch is
called). `--provider anthropic` talks to Anthropic directly instead of
OpenRouter — that path is less battle-tested than OpenRouter (all our live
runs so far went through OpenRouter); file an issue if you hit trouble there.

## GitHub Action

The `action.yml` workflow file in this repository is a mutation-testing gate for pull requests. It mutates only what changed in the PR and posts survivors as a comment, editing the same comment on each push instead of creating new ones.

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
      - uses: Ilyat9/mutagen-cli@v0
        with:
          provider: openrouter
          openrouter-api-key: ${{ secrets.OPENROUTER_API_KEY }}
          fail-under: "70"
          invent: "true"
```

⚠️ **Important security note:** `pip install` executes code from the PR (setup.py, build hooks) in the runner's main process before mutagen has a chance to strip secrets, so for repositories that accept PRs from forks, you **must enable** "Require approval for first-time contributors" in Settings → Actions → General.

**Critical:** Use only `pull_request` trigger as shown above. Never use `pull_request_target` with code checkout. `pull_request_target` runs with access to your repository's secrets, but it executes the PR's code — which you don't control. A fork PR can change anything the workflow runs, including the test suite itself, and exfiltrate `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, or `GITHUB_TOKEN` before mutagen can strip them. This is a classic pwn request. The `pull_request` trigger doesn't have this vulnerability: it has no access to repository secrets when running on fork PRs.

Tests from the PR run without secrets in the environment: mutagen strips `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, and `GITHUB_TOKEN` from the environment of each pytest subprocess, so untrusted test code cannot read them. This protects against malicious tests, but it is not a substitute for `pull_request_target` protection — always require approval for first-time contributors on fork PRs, just as you would for any CI that runs untrusted code.

## Limitations

- **Python and pytest only.** Other languages and runners are not supported.
- **Cost is real.** One LLM call per changed function, plus one more per
  survivor under `--invent`. The disk cache makes reruns cheap, but the first
  run on a large diff won't be free. `--dry-run` shows the call count upfront.
- **Equivalent mutants still slip through.** The prompt actively forbids
  mutations that don't change behavior, and most survivors are real bugs, but
  not all. A "survivor" is a lead to check, not a proven gap.
- **Precise test mapping needs `pytest-cov`** in the interpreter that runs
  your tests. With it, mutagen-cli measures which tests execute which lines.
  Without it, mutagen-cli falls back to a filename/symbol heuristic and says
  so in the report; a heuristic that guesses the wrong files reports mutants
  as survivors even though the test that would have killed them just never ran.
- **Each worker copies the repository** into a temp directory. Large repos
  with large untracked directories will feel it.
- **Your working tree is never touched** — except by `--invent-apply`, which
  writes new files to `tests/mutagen_generated/` and nowhere else.
- **This is not a coverage tool.** A high mutation score on the functions you
  changed says nothing about the functions you didn't touch.
- **Report text is LLM-generated from your code, including untrusted code.**
  Each mutant's description and other report text is written by the model
  from the function's source (and, under `--invent`, its tests) — which may
  be code from someone else's PR. That text is posted into the markdown/JSON
  report and the PR comment with sanitization against prompt-injection vectors
  (image-beacon syntax, HTML tags). However, a comment or docstring in the PR
  crafted to influence the model could in principle change the wording in the
  report. Keep that in mind when the Action runs on third-party PRs: the report
  is text generated from untrusted input, not a statement from your CI system.
- **Projects whose package only imports from site-packages** (e.g. with
  C extensions built at install time) aren't a fit yet: mutagen-cli puts the
  worker copy's sources ahead on `PYTHONPATH`, and the copy has no compiled
  modules — the baseline fails with `ImportError`.
- **The secret strip-list for pytest subprocesses is fixed** —
  `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`. Other
  environment tokens (`AWS_*`, `NPM_TOKEN`, etc.) are visible to pytest
  subprocesses; don't run this on untrusted code with such variables in the
  environment.

Full docs, provider setup, CI/GitHub Action, flags, benchmarks, and the
project's origin story (two separate bugs in the tool itself, caught by hand,
along the way): see the
[Russian README](README.md#история-проекта) —
it's the maintained one.

## License

MIT
