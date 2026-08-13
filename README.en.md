Full version (Russian): [README.md](README.md)

# mutagen-cli

[![CI](https://github.com/Ilyat9/mutagen-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/Ilyat9/mutagen-cli/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mutagen-cli)](https://pypi.org/project/mutagen-cli/)

**Your tests are green. Here's what they don't catch.**

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

(`mutagen-cli` is the distribution name — `mutagen` itself is the
audio-metadata library; the command installed is `mutagen`.) To work on
mutagen-cli itself instead: `git clone https://github.com/Ilyat9/mutagen-cli
&& cd mutagen-cli && pip install -e ".[dev]"`.

No config file. `mutagen run` diffs your working tree against `main`, mutates
only the functions you changed, and runs only the tests that actually cover
them. `--provider anthropic` talks to Anthropic directly instead of
OpenRouter.

## Limitations

Python/pytest only; cost is real (one LLM call per changed function, more
with `--invent`, though the disk cache makes reruns cheap); equivalent
mutants still slip through so a survivor is a lead, not a proof; precise test
mapping needs `pytest-cov`, otherwise it falls back to a filename heuristic
and says so; each worker copies your repo, so very large repos feel it; your
working tree itself is never touched except by `--invent-apply`; and it's not
a coverage tool — a high score on the functions you changed says nothing
about the ones you didn't.

Full docs, provider setup, CI/GitHub Action, flags, benchmarks, and the
project's origin story (two separate bugs in the tool itself, caught by hand,
along the way): see the
[Russian README](README.md#история-проекта) —
it's the maintained one.

## License

MIT
