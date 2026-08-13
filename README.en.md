Full version (Russian): [README.md](README.md)

# mutagen

**Your tests are green. Here's what they don't catch.**

mutagen introduces realistic bugs into your code — off-by-one errors, missed
cache invalidation, swapped arguments, inverted conditions — and reruns your
test suite. Any bug that survives is a gap in your tests, reported as the
concrete failure your users will hit. The mutants are written by an LLM that
has read both your function *and* the tests covering it, so it aims at the
blind spots instead of flipping operators at random.

Real output, excerpted from a run against a third-party repo
([CogniWeb_Agent](https://github.com/Ilyat9/CogniWeb_Agent), 19 mocked tests,
all green):

<img src="assets/mutagen_report.svg" alt="mutagen run: mutation score 12%, two survivors — an off-by-one in history trimming and an inverted captcha check" width="900">

## Quickstart

Not on PyPI yet — install from source (`mutagen-cli` is the reserved
distribution name; the command is `mutagen`).

```bash
git clone https://github.com/mutagen-cli/mutagen && cd mutagen && pip install -e .
export OPENROUTER_API_KEY=sk-or-...
mutagen run
```

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

Full docs, provider setup, CI/GitHub Action, flags, and benchmarks: see the
[Russian README](README.md) — it's the maintained one.

## License

MIT
