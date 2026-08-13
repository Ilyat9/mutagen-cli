"""Applying mutants in isolated copies of the repo and running pytest."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import coverage_map
from .apply import apply_search_replace, make_diff
from .coverage_map import CoverageMap
from .models import ERROR, KILLED, SURVIVED, TIMEOUT, UNAPPLICABLE, Mutant, Result
from .scope import SKIP_DIRS

# pytest exit codes we can interpret.
EXIT_OK = 0
EXIT_TESTS_FAILED = 1

# Test code in the mutated repo is untrusted — on the GitHub Action it is
# whatever a pull request author wrote. mutagen's own credentials have no
# business being readable from inside a pytest subprocess: a test could print
# one into an assertion failure, which we then capture and put in a PR
# comment. Strip them rather than trust the test suite not to look.
SECRET_ENV_VARS = ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN")


@dataclass
class RunnerConfig:
    root: Path
    python: str
    timeout: float = 30.0
    workers: int = 1
    pytest_args: list[str] = field(default_factory=list)


def detect_python(root: Path) -> str:
    """Prefer the project's own virtualenv — mutagen may be installed elsewhere."""
    for candidate in (".venv", "venv", "env"):
        for exe in ("bin/python", "Scripts/python.exe"):
            path = root / candidate / exe
            if path.exists():
                return str(path)
    return sys.executable


def default_workers() -> int:
    return max(1, (os.cpu_count() or 2) // 2)


def _ignore(directory, names):
    # "migrations" is excluded from mutation *scope* (alembic-style files are
    # not worth mutating), but the worker copy must be faithful: a package that
    # ships SQL as data (e.g. src/pkg/storage/migrations/*.sql) breaks without
    # it, and the baseline goes red for reasons unrelated to the suite.
    return {name for name in names if name in (SKIP_DIRS - {"migrations"})}


def prepare_workspace(root: Path, dest: Path) -> None:
    shutil.copytree(root, dest, ignore=_ignore, symlinks=True)


def run_pytest(
    workdir: Path,
    python: str,
    test_paths: list[str],
    timeout: float,
    extra_args: Optional[list[str]] = None,
    collect_coverage: bool = False,
) -> tuple[int, str, float]:
    cmd = [
        python, "-m", "pytest",
        *test_paths,
        "-q", "--no-header", "-p", "no:cacheprovider",
        *(extra_args or []),
    ]
    if collect_coverage:
        coverage_map.write_config(workdir)
        cmd.extend(coverage_map.pytest_args(workdir))
    # Without this, a mutation that does not change a file's size (min -> max,
    # < -> <=) can be masked by a .pyc written in the same second: CPython
    # invalidates on mtime+size, so the stale bytecode is reused and the mutant
    # silently never runs. That would report a survivor that isn't one.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for var in SECRET_ENV_VARS:
        env.pop(var, None)
    # A src-layout project installed editable (pip install -e .) adds the
    # ORIGINAL tree to sys.path via a .pth in site-packages, so tests in the
    # worker copy import unmutated code and every mutant looks like a survivor.
    # Put the worker copy first on PYTHONPATH: it precedes site-packages, so
    # the mutated package shadows the editable install.
    shadow = [str(workdir / "src"), str(workdir)]
    if os.environ.get("PYTHONPATH"):
        shadow.append(os.environ["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(shadow)
    if collect_coverage:
        env["COVERAGE_FILE"] = str(workdir / coverage_map.DATA_FILE)
        env["COVERAGE_CORE"] = coverage_map.COVERAGE_CORE

    # So a runaway mutant's children die with it: a new session on POSIX
    # (killpg reaches the whole group), a new process group on Windows
    # (taskkill /T reaches the whole tree).
    group_kwargs = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=str(workdir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        env=env,
        **group_kwargs,
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
        output, _ = proc.communicate()
        code = -1
    return code, output or "", time.monotonic() - started


def _selection(mutants: list[Mutant]) -> list[str]:
    """Union of the tests mapped to these mutants.

    Empty means "no mapping for at least one target", i.e. run everything.
    """
    selected: list[str] = []
    for mutant in mutants:
        if not mutant.target.selection:
            return []
        for rel in mutant.target.selection:
            if rel not in selected:
                selected.append(rel)
    return selected


@dataclass
class Baseline:
    green: bool
    output: str
    duration: float
    test_paths: list[str]

    @property
    def pytest_missing(self) -> bool:
        """`python -m pytest` without pytest exits 1, same as a failing suite.

        Telling the user their suite is red when the runner simply is not
        installed sends them looking for a bug that is not there.
        """
        return "No module named pytest" in self.output


def check_baseline(
    cfg: RunnerConfig,
    mutants: list[Mutant],
    workdir: Path,
    collect_coverage: bool = False,
) -> Baseline:
    """Run the tests unmutated.

    If the suite is already red, mutation numbers are meaningless — every
    mutant would look killed. Better to stop and say so.

    With `collect_coverage` this is also where the per-test coverage map comes
    from, which means running the WHOLE suite: a map built from a subset could
    only ever tell us about the tests we already guessed at.
    """
    paths = [] if collect_coverage else _selection(mutants)
    code, output, duration = run_pytest(
        workdir, cfg.python, paths, max(cfg.timeout * 4, 300.0), cfg.pytest_args,
        collect_coverage=collect_coverage,
    )
    return Baseline(code == EXIT_OK, output, duration, paths)


def collect_coverage_map(cfg: RunnerConfig, workdir: Path) -> tuple[Baseline, CoverageMap]:
    """Instrumented baseline plus the map it produced.

    One entry point so the CLI and `scripts/benchmark.py` build the map the
    same way — the map feeds the generation prompt, so a benchmark that mapped
    differently would prompt differently and miss the cache.
    """
    baseline = check_baseline(cfg, [], workdir, collect_coverage=True)
    if not baseline.green:
        return baseline, CoverageMap()
    return baseline, coverage_map.load(workdir, cfg.root)


def summarize_failures(output: str, limit: int = 4) -> str:
    lines = [
        line.strip()
        for line in output.splitlines()
        if line.startswith(("FAILED ", "ERROR "))
    ]
    seen: list[str] = []
    for line in lines:
        if line not in seen:
            seen.append(line)
    if seen:
        return "; ".join(seen[:limit])
    tail = [line.strip() for line in output.strip().splitlines() if line.strip()]
    return tail[-1] if tail else ""


def _verdict(code: int) -> str:
    if code == EXIT_OK:
        return SURVIVED
    if code == EXIT_TESTS_FAILED:
        return KILLED
    if code == -1:
        return TIMEOUT
    # 2 interrupted / collection error, 3 internal, 4 usage, 5 no tests collected.
    return ERROR


def execute(
    cfg: RunnerConfig,
    mutants: list[Mutant],
    workdirs: list[Path],
    on_result: Optional[Callable[[Result], None]] = None,
    coverage: Optional[CoverageMap] = None,
) -> list[Result]:
    results: list[Result] = []
    lock = threading.Lock()

    def process(workdir: Path, batch: list[Mutant]) -> None:
        originals: dict[str, str] = {}
        for mutant in batch:
            rel = mutant.target.path
            file_path = workdir / rel
            if rel not in originals:
                try:
                    originals[rel] = file_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    _record(Result(mutant, ERROR, detail=f"cannot read {rel}: {exc}"))
                    continue
            original = originals[rel]

            # Confine the edit to the function we asked about. Without the
            # span, a block that also occurs in a neighbouring function is
            # applied there instead, and we then run the *target's* tests
            # against an untouched target — a survivor that is pure artefact.
            applied = apply_search_replace(
                original,
                mutant.search_block,
                mutant.replace_block,
                span=(mutant.target.start_line, mutant.target.end_line),
            )
            if not applied.ok:
                _record(Result(mutant, UNAPPLICABLE, detail=applied.reason))
                continue

            # The model was told to match exactly once; when the search block
            # is not unique within the function, apply.py silently takes the
            # first hit. That's a real risk of mutating the wrong occurrence,
            # so it must survive into the report, not get discarded here.
            match_note = applied.method if applied.method.startswith("exact (") else ""

            diff = make_diff(rel, original, applied.text)

            # Which tests actually reach the lines this mutation changed?
            paths = mutant.target.selection
            if coverage and coverage.knows(rel):
                covering = coverage.tests_for(rel, applied.start_line, applied.end_line)
                if not covering:
                    # Nothing executes these lines, so no test outcome can
                    # depend on them. Running the suite to watch it pass would
                    # only cost time and prove what the map already states.
                    detail = "no test executes the mutated lines"
                    if match_note:
                        detail = f"[{match_note}] {detail}"
                    _record(
                        Result(
                            mutant,
                            SURVIVED,
                            detail=detail,
                            diff=diff,
                            no_coverage=True,
                        )
                    )
                    continue
                paths = coverage_map.selection_for(covering)

            file_path.write_text(applied.text, encoding="utf-8")
            try:
                code, output, duration = run_pytest(
                    workdir, cfg.python, paths, cfg.timeout, ["-x", *cfg.pytest_args]
                )
            finally:
                file_path.write_text(original, encoding="utf-8")

            verdict = _verdict(code)
            detail = ""
            if verdict == KILLED:
                detail = summarize_failures(output)
            elif verdict == TIMEOUT:
                detail = f"no result within {cfg.timeout:.0f}s (probable infinite loop)"
            elif verdict == ERROR:
                detail = f"pytest exit {code}: {summarize_failures(output)}"
            if match_note:
                detail = f"[{match_note}] {detail}".strip()
            _record(Result(mutant, verdict, detail=detail, diff=diff, duration=duration))

    def _record(result: Result) -> None:
        with lock:
            results.append(result)
            if on_result:
                on_result(result)

    batches = [mutants[i :: len(workdirs)] for i in range(len(workdirs))]
    with ThreadPoolExecutor(max_workers=len(workdirs)) as pool:
        futures = [
            pool.submit(process, workdir, batch)
            for workdir, batch in zip(workdirs, batches)
            if batch
        ]
        for future in futures:
            future.result()

    order = {id(m): i for i, m in enumerate(mutants)}
    results.sort(key=lambda r: order.get(id(r.mutant), 0))
    return results
