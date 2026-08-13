"""Guards the shape of pyproject.toml's own dependency list.

`coverage_map.load` runs `import coverage` inside mutagen's own interpreter,
not the target project's — it reads the data file the instrumented baseline
just wrote. A plain `pip install mutagen-cli` only pulls in `dependencies`,
never the `dev` extra, so if `coverage` is not a direct dependency, a user
with `pytest-cov` correctly installed in *their* venv still gets a silent
ImportError inside mutagen, an empty map, and the misleading "install
pytest-cov" heuristic message even though pytest-cov is already there.
"""

import re
from importlib.metadata import version
from pathlib import Path

import mutagen_cli

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_coverage_is_a_direct_dependency_not_only_a_dev_extra():
    text = PYPROJECT.read_text(encoding="utf-8")
    deps_block = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL).group(1)
    names = [
        line.strip().strip('",').split(">=")[0].split("==")[0]
        for line in deps_block.splitlines()
        if line.strip().strip(",")
    ]
    assert "coverage" in names, (
        "mutagen_cli.coverage_map.load() imports `coverage` in mutagen's own "
        "interpreter and must not depend on it arriving transitively via the "
        "dev extra's pytest-cov"
    )


def test_reported_version_matches_pyproject():
    """`mutagen --version` must never drift from what PyPI actually shipped.

    mutagen_cli.__version__ is read from installed package metadata, so a
    mismatch here means pyproject.toml's version was bumped without
    reinstalling — the same failure mode this test exists to catch.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert match, "pyproject.toml has no top-level version field"
    pyproject_version = match.group(1)

    assert version("mutagen-cli") == pyproject_version
    assert mutagen_cli.__version__ == pyproject_version
