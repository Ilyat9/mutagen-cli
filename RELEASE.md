# Releasing

## Before the first release

Push to GitHub first — the README's install line, `action.yml` and the
`Homepage` in `pyproject.toml` all point at `github.com/mutagen-cli/mutagen`.

```bash
git remote add origin git@github.com:mutagen-cli/mutagen.git
git push -u origin main
```

Then claim the name on PyPI. `mutagen-cli` was free as of 2026-08-13 (`mutagen`
itself is the audio-metadata library and is not ours). Register at
<https://pypi.org/account/register/>, then create a **project-scoped API token**
under Account settings → API tokens.

## Cutting a release

```bash
python -m pip install --upgrade build twine
rm -rf dist/
python -m build
twine check dist/*
```

Both artefacts must pass `twine check`. Sanity-check the wheel in a throwaway
environment before uploading — this is the step that catches a broken entry
point or a missing module:

```bash
python -m venv /tmp/relcheck && /tmp/relcheck/bin/pip install dist/*.whl
/tmp/relcheck/bin/mutagen --version
```

Upload to TestPyPI first if you want a dry run, then the real thing:

```bash
twine upload --repository testpypi dist/*
twine upload dist/*
```

`twine` will ask for a username of `__token__` and the API token as the
password.

## After uploading

1. Change the README quickstart from the `git clone` line back to
   `pip install mutagen-cli`.
2. Change `action.yml`'s install step from `pip install "${{ github.action_path }}"`
   back to `pip install mutagen-cli` (the comment there says the same).
3. Update DECISIONS.md D1, which currently records that nothing is published.
4. Tag it: `git tag -a v0.1.0 -m "0.1.0" && git push --tags`, and move the
   `v0` tag the README's workflow example uses.

## Verified locally on 2026-08-13

- `python -m build` → `mutagen_cli-0.1.0-py3-none-any.whl` + sdist.
- `twine check dist/*` → both PASSED.
- Wheel contains only `mutagen_cli/*`, the `mutagen = mutagen_cli.cli:main`
  console script, and `licenses/LICENSE`. No stray files.
- Installed into a clean venv: `mutagen --version` works.
- Installed *alongside* the PyPI `mutagen` audio package: both `import mutagen`
  and `import mutagen_cli` work, and the `mutagen` command still resolves to
  this tool. The audio package ships `mid3v2`, `mutagen-inspect` and friends,
  never a bare `mutagen`, so there is no console-script collision.

Not done here: the actual `twine upload`. That publishes irreversibly under
your account and needs your PyPI token.
