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

from .apply import apply_search_replace, make_diff
from .models import ERROR, KILLED, SURVIVED, TIMEOUT, UNAPPLICABLE, Mutant, Result
from .scope import SKIP_DIRS

# pytest exit codes we can interpret.
EXIT_OK = 0
EXIT_TESTS_FAILED = 1


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
) -> tuple[int, str, float]:
    cmd = [
        python, "-m", "pytest",
        *test_paths,
        "-q", "--no-header", "-p", "no:cacheprovider",
        *(extra_args or []),
    ]
    # Without this, a mutation that does not change a file's size (min -> max,
    # < -> <=) can be masked by a .pyc written in the same second: CPython
    # invalidates on mtime+size, so the stale bytecode is reused and the mutant
    # silently never runs. That would report a survivor that isn't one.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    # A src-layout project installed editable (pip install -e .) adds the
    # ORIGINAL tree to sys.path via a .pth in site-packages, so tests in the
    # worker copy import unmutated code and every mutant looks like a survivor.
    # Put the worker copy first on PYTHONPATH: it precedes site-packages, so
    # the mutated package shadows the editable install.
    shadow = [str(workdir / "src"), str(workdir)]
    if os.environ.get("PYTHONPATH"):
        shadow.append(os.environ["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(shadow)

    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=str(workdir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        env=env,
        start_new_session=True,  # so a runaway mutant's children die with it
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        output, _ = proc.communicate()
        code = -1
    return code, output or "", time.monotonic() - started


def _selection(mutants: list[Mutant]) -> list[str]:
    """Union of the test files mapped to these mutants.

    Empty means "no mapping for at least one target", i.e. run everything.
    """
    selected: list[str] = []
    for mutant in mutants:
        if not mutant.target.test_files:
            return []
        for rel in mutant.target.test_files:
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


def check_baseline(cfg: RunnerConfig, mutants: list[Mutant], workdir: Path) -> Baseline:
    """Run the selected tests unmutated.

    If the suite is already red, mutation numbers are meaningless — every
    mutant would look killed. Better to stop and say so.
    """
    paths = _selection(mutants)
    code, output, duration = run_pytest(
        workdir, cfg.python, paths, max(cfg.timeout * 4, 120.0), cfg.pytest_args
    )
    return Baseline(code == EXIT_OK, output, duration, paths)


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

            diff = make_diff(rel, original, applied.text)
            file_path.write_text(applied.text, encoding="utf-8")
            try:
                paths = mutant.target.test_files or []
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
