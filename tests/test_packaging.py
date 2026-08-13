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
from pathlib import Path

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
