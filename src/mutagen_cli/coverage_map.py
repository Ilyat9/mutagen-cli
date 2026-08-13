"""Which tests execute which lines.

The heuristic mapping in `scope.map_tests` (filename similarity plus symbol
references) is a guess. When it guesses wrong the mutant runs against tests
that could not possibly kill it and is reported as a survivor — a false
positive in exactly the direction that makes the tool untrustworthy.

Coverage answers the question directly. One instrumented baseline run records,
per line, the tests that executed it; every mutant afterwards is run against
the tests that touch the lines it changed, and a mutation on lines no test
executes is a survivor we do not even have to run.

The map is built once per `mutagen run` — the code under test does not change
between mutants, only the mutation does, and each mutant is applied to a
pristine copy.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# coverage's `sysmon` core — the default on Python 3.12+ — disables line events
# once a line has been seen, so only the FIRST test to execute a line is
# recorded against it. That silently produces a map missing most of its
# contexts, which is worse than no map at all: it would send us to run a subset
# of the covering tests and report false survivors. `ctrace` records every
# (line, context) pair. Verified on coverage 7.15 / CPython 3.14; there is a
# regression test that fails if this is removed.
COVERAGE_CORE = "ctrace"

DATA_FILE = ".mutagen-coverage"
# A pytest command line has limits, and a mutant covered by three thousand
# tests is not a useful selection anyway — fall back to whole files there.
MAX_NODEIDS = 200


@dataclass
class CoverageMap:
    """Line -> covering test node ids, per repo-relative source file."""

    lines: dict[str, dict[int, set[str]]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.lines)

    def tests_for(self, rel: str, start_line: int, end_line: int) -> list[str]:
        """Test node ids executing any line in [start_line, end_line]."""
        by_line = self.lines.get(rel)
        if not by_line:
            return []
        hits: set[str] = set()
        for line in range(start_line, end_line + 1):
            hits |= by_line.get(line, set())
        return sorted(hits)

    def knows(self, rel: str) -> bool:
        """True when this file was measured at all.

        A file missing from the map was never imported by the suite, which is
        different from "measured and covered by nothing" only in that we cannot
        tell the two apart — both mean no test executes it.
        """
        return rel in self.lines


def selection_for(tests: list[str]) -> list[str]:
    """Node ids to pass to pytest, collapsed to files when there are too many."""
    if len(tests) <= MAX_NODEIDS:
        return tests
    files: list[str] = []
    for nodeid in tests:
        path = nodeid.split("::")[0]
        if path not in files:
            files.append(path)
    return sorted(files)


def files_in(tests: list[str]) -> list[str]:
    """The distinct test files behind a set of node ids (for prompt context)."""
    files: list[str] = []
    for nodeid in tests:
        path = nodeid.split("::")[0]
        if path not in files:
            files.append(path)
    return files


def available(python: str) -> bool:
    """Can `python` collect per-test coverage contexts?"""
    probe = subprocess.run(
        [python, "-c", "import pytest_cov, coverage"],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def pytest_args(workdir: Path) -> list[str]:
    return [
        "--cov=.",
        "--cov-context=test",
        "--cov-report=",
        f"--cov-config={workdir / '.mutagen-coveragerc'}",
    ]


def write_config(workdir: Path) -> None:
    """A config of our own, so the project's own coverage settings — omit
    lines, `fail_under`, a `parallel` mode we would then have to combine —
    cannot turn the map into something we misread."""
    (workdir / ".mutagen-coveragerc").write_text(
        "[run]\nbranch = False\nparallel = False\nrelative_files = True\n"
        "\n[report]\nfail_under = 0\n",
        encoding="utf-8",
    )


def load(workdir: Path, root: Path) -> CoverageMap:
    """Read the data file written by the instrumented baseline run."""
    try:
        import coverage
    except ImportError:  # pragma: no cover - only when we are not the runner
        return CoverageMap()

    path = workdir / DATA_FILE
    if not path.exists():
        return CoverageMap()

    data = coverage.CoverageData(str(path))
    try:
        data.read()
    except Exception:  # noqa: BLE001 - a corrupt data file is just "no map"
        return CoverageMap()

    lines: dict[str, dict[int, set[str]]] = {}
    for measured in data.measured_files():
        rel = _relative(measured, workdir, root)
        if rel is None:
            continue
        by_line: dict[int, set[str]] = {}
        for line, contexts in data.contexts_by_lineno(measured).items():
            tests = {_nodeid(c) for c in contexts if c}
            tests.discard("")
            if tests:
                by_line[line] = tests
        lines[rel] = by_line
    return CoverageMap(lines)


def map_targets(cmap: CoverageMap, targets: list) -> tuple[int, int]:
    """Replace the heuristic mapping with the measured one, in place.

    Returns `(refined, uncovered)`. A file the map never saw keeps its
    heuristic mapping: "absent from the map" means "never imported by the
    suite" *or* "excluded by a source filter", and we cannot tell those apart
    from here. Only a file that was measured and whose lines came back with no
    contexts is treated as genuinely uncovered.
    """
    refined = uncovered = 0
    for target in targets:
        if not cmap.knows(target.path):
            continue
        tests = cmap.tests_for(target.path, target.start_line, target.end_line)
        target.test_files = files_in(tests)
        target.test_selection = selection_for(tests)
        refined += 1
        if not tests:
            uncovered += 1
    return refined, uncovered


def _nodeid(context: str) -> str:
    """`tests/test_x.py::test_y|run` -> `tests/test_x.py::test_y`.

    pytest-cov appends the test phase; setup and teardown coverage counts just
    as much as the call phase, so the phase is dropped rather than filtered.
    """
    return context.split("|")[0]


def _relative(measured: str, workdir: Path, root: Path) -> Optional[str]:
    """Map a path from the coverage data back to a repo-relative path."""
    candidate = Path(measured)
    for base in (workdir, root):
        try:
            return candidate.relative_to(base).as_posix()
        except ValueError:
            continue
    if not candidate.is_absolute():
        return candidate.as_posix()  # relative_files = True
    return None
